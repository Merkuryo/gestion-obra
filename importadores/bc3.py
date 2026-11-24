"""Lector de ficheros BC3 (formato FIEBDC-3): solo lectura, sin Django ni base de datos.

Uso:
    resultado = leer_bc3("presupuesto.bc3")      # ruta, o los bytes del fichero
    resultado.raiz, resultado.capitulos, resultado.partidas, resultado.descompuestos,
    resultado.mediciones, resultado.recuento(), resultado.avisos, resultado.errores

Formato (norma FIEBDC-3, versiones 2007, 2012, 2016 y 2020):
- Cada registro empieza por `~` y una letra; los campos se separan con `|` y los subcampos
  con `\\`. Los saltos de línea dentro de un registro no cuentan (salvo en el texto de ~T).
- Registros que se leen: ~V cabecera · ~K decimales y porcentajes · ~C conceptos ·
  ~D descomposiciones · ~T textos largos · ~M mediciones. Los demás se ignoran con un aviso.
- Juego de caracteres según ~V: ANSI (cp1252), 850 o 437. Sin indicar, cp1252.
- Un código que acaba en `##` es la raíz; en `#`, un capítulo.

Árbol: raíz → capítulos (y subcapítulos) → partidas → descompuestos, que a su vez pueden
descomponerse (auxiliares, a cualquier nivel). Una partida es todo concepto que no es capítulo
y cuelga de un capítulo o de la raíz; lo que cuelga de una partida es un descompuesto.

Nunca lanza excepciones por el contenido del fichero: lo que no se puede leer queda en
`errores` (se ha perdido información) y lo dudoso en `avisos`, con el número de línea.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

VERSIONES = ("FIEBDC-3/2007", "FIEBDC-3/2012", "FIEBDC-3/2016", "FIEBDC-3/2020")
JUEGOS_DE_CARACTERES = {"ANSI": "cp1252", "850": "cp850", "437": "cp437"}
CODIFICACION_POR_DEFECTO = "cp1252"

TIPOS_CONCEPTO = {
    "0": "Sin clasificar",
    "1": "Mano de obra",
    "2": "Maquinaria y medios auxiliares",
    "3": "Materiales",
    "4": "Componentes adicionales de residuo",
    "5": "Clasificación de residuo",
}
TIPOS_INFORMACION = {
    "1": "Base de datos",
    "2": "Presupuesto",
    "3": "Certificación",
    "4": "Actualización de base de datos",
}
REGISTROS_LEIDOS = ("V", "K", "C", "D", "T", "M")

# Subcampos de ~K, con los nombres de la norma
DECIMALES_CAMPO_1 = ("DN", "DD", "DS", "DR", "DI", "DP", "DC", "DM")  # por compatibilidad
DECIMALES_CAMPO_3 = (
    "DRC",
    "DC",
    "DFS",
    "DRS",
    "DFO",
    "DRO",
    "DI",
    "DES",
    "DN",
    "DD",
    "DS",
    "DSP",
    "DEC",
)
PORCENTAJES_K = ("CI", "GG", "BI", "BAJA", "IVA")
DECIMALES_MEDICION_POR_DEFECTO = 2

# Tipos de línea de medición
LINEA_NORMAL, SUBTOTAL_PARCIAL, SUBTOTAL_ACUMULADO, EXPRESION = "", "1", "2", "3"

ERROR, AVISO = "error", "aviso"
MAX_NIVELES = 100  # niveles del árbol; más allá, el fichero está mal hecho


# ── Resultado ──────────────────────────────────────────────────────────────────


@dataclass
class Incidencia:
    """Un aviso o un error del fichero, con su línea (None si es del fichero entero)."""

    nivel: str  # ERROR | AVISO
    mensaje: str
    linea: int | None = None

    def __str__(self):
        donde = f"línea {self.linea}: " if self.linea else ""
        return f"{donde}{self.mensaje}"


@dataclass
class Concepto:
    """Registro ~C, con su texto largo (~T) si lo tiene."""

    codigo: str
    unidad: str = ""
    resumen: str = ""
    precio: Decimal | None = None  # el primero de la lista de precios
    precios: list[Decimal] = field(default_factory=list)
    fecha: date | None = None
    tipo: str = "0"
    texto: str = ""
    sinonimos: list[str] = field(default_factory=list)
    linea: int | None = None

    @property
    def es_raiz(self) -> bool:
        return self.codigo.endswith("##")

    @property
    def es_capitulo(self) -> bool:
        return self.codigo.endswith("#") and not self.es_raiz

    @property
    def nombre_tipo(self) -> str:
        return TIPOS_CONCEPTO.get(self.tipo, f"Tipo {self.tipo}")


@dataclass
class Componente:
    """Una línea de una descomposición ~D: hijo, factor y rendimiento."""

    codigo: str
    factor: Decimal = Decimal("1")
    rendimiento: Decimal = Decimal("1")
    porcentajes: list[str] = field(default_factory=list)  # códigos de % (~D campo 3)


@dataclass
class LineaMedicion:
    tipo: str = LINEA_NORMAL
    comentario: str = ""
    unidades: Decimal | None = None
    longitud: Decimal | None = None
    latitud: Decimal | None = None
    altura: Decimal | None = None

    @property
    def parcial(self) -> Decimal | None:
        """Producto de las dimensiones indicadas (las vacías no cuentan). None en subtotales,
        expresiones y líneas solo de comentario."""
        if self.tipo != LINEA_NORMAL:
            return None
        valores = [
            v for v in (self.unidades, self.longitud, self.latitud, self.altura) if v is not None
        ]
        if not valores:
            return None
        resultado = Decimal("1")
        for valor in valores:
            resultado *= valor
        return resultado


@dataclass
class Medicion:
    """Registro ~M: la medición de `codigo` dentro de `padre`."""

    padre: str
    codigo: str
    total: Decimal | None  # el del fichero; si no viene, el calculado
    total_calculado: Decimal | None  # suma de las líneas, redondeada según ~K
    lineas: list[LineaMedicion] = field(default_factory=list)
    posicion: list[str] = field(default_factory=list)
    etiqueta: str = ""
    linea: int | None = None


@dataclass
class Nodo:
    """Un concepto en su sitio del árbol, con el factor y el rendimiento con que cuelga de su
    padre. Un mismo concepto (un auxiliar) puede aparecer en varios nodos."""

    concepto: Concepto
    clase: str  # "raiz" | "capitulo" | "partida" | "descompuesto"
    factor: Decimal = Decimal("1")
    rendimiento: Decimal = Decimal("1")
    nivel: int = 0
    padre: Nodo | None = field(default=None, repr=False)
    hijos: list[Nodo] = field(default_factory=list)
    medicion: Medicion | None = None

    @property
    def codigo(self):
        return self.concepto.codigo


@dataclass
class Decimales:
    """Registro ~K. `campo_1` (por compatibilidad) y `campo_3` (versiones actuales) guardan
    los decimales con los nombres de la norma; `porcentajes`, CI, GG, BI, BAJA e IVA."""

    campo_1: dict[str, int] = field(default_factory=dict)
    campo_3: dict[str, int] = field(default_factory=dict)
    divisa: str = ""
    porcentajes: dict[str, Decimal] = field(default_factory=dict)

    def _valor(self, nombre):
        for campo in (self.campo_3, self.campo_1):
            if nombre in campo:
                return campo[nombre]
        return DECIMALES_MEDICION_POR_DEFECTO

    @property
    def medicion_total(self) -> int:
        """DS: decimales del total de una medición."""
        return self._valor("DS")


@dataclass
class ResultadoBC3:
    version: str = ""
    fecha_version: date | None = None
    programa: str = ""
    propietario: str = ""
    cabecera: str = ""
    rotulos: list[str] = field(default_factory=list)
    juego_de_caracteres: str = ""  # el que dice ~V
    codificacion: str = CODIFICACION_POR_DEFECTO  # con la que se ha leído
    comentario: str = ""
    tipo_informacion: str = ""
    decimales: Decimales = field(default_factory=Decimales)
    conceptos: dict[str, Concepto] = field(default_factory=dict)
    descomposiciones: dict[str, list[Componente]] = field(default_factory=dict)
    raiz: Nodo | None = None
    capitulos: list[Nodo] = field(default_factory=list)
    partidas: list[Nodo] = field(default_factory=list)
    lineas_descompuestas: list[Nodo] = field(default_factory=list)
    mediciones: list[Medicion] = field(default_factory=list)
    incidencias: list[Incidencia] = field(default_factory=list)

    @property
    def avisos(self) -> list[Incidencia]:
        return [i for i in self.incidencias if i.nivel == AVISO]

    @property
    def errores(self) -> list[Incidencia]:
        return [i for i in self.incidencias if i.nivel == ERROR]

    @property
    def es_valido(self) -> bool:
        return not self.errores

    @property
    def descompuestos(self) -> list[Concepto]:
        """Conceptos distintos que aparecen como descompuestos, en orden de aparición."""
        vistos = {}
        for nodo in self.lineas_descompuestas:
            vistos.setdefault(nodo.codigo, nodo.concepto)
        return list(vistos.values())

    @property
    def nombre_tipo_informacion(self) -> str:
        return TIPOS_INFORMACION.get(self.tipo_informacion, self.tipo_informacion)

    def recuento(self) -> dict[str, int]:
        return {
            "conceptos": len(self.conceptos),
            "capitulos": len(self.capitulos),
            "partidas": len(self.partidas),
            "descompuestos": len(self.descompuestos),
            "lineas_descompuestas": len(self.lineas_descompuestas),
            "mediciones": len(self.mediciones),
            "textos": sum(1 for c in self.conceptos.values() if c.texto),
            "avisos": len(self.avisos),
            "errores": len(self.errores),
        }


# ── Lectura ────────────────────────────────────────────────────────────────────


def leer_bc3(origen: bytes | str | Path) -> ResultadoBC3:
    """Lee un BC3 desde una ruta o desde sus bytes. No guarda nada."""
    if isinstance(origen, bytes):
        return _Lector(origen).leer()
    try:
        datos = Path(origen).read_bytes()
    except OSError as error:
        resultado = ResultadoBC3()
        resultado.incidencias.append(
            Incidencia(ERROR, f"No se puede leer el fichero: {error.strerror or error}")
        )
        return resultado
    return _Lector(datos).leer()


@dataclass
class _Registro:
    tipo: str
    campos: list[str]  # sin la letra del tipo
    linea: int


class _Lector:
    def __init__(self, datos: bytes):
        self.datos = datos
        self.r = ResultadoBC3()
        self._saltos: list[int] = []
        self._lineas_d: dict[str, int] = {}  # línea de cada ~D, para los avisos del árbol
        self._textos: dict[str, tuple[str, int]] = {}

    # Incidencias
    def error(self, mensaje, linea=None):
        self.r.incidencias.append(Incidencia(ERROR, mensaje, linea))

    def aviso(self, mensaje, linea=None):
        self.r.incidencias.append(Incidencia(AVISO, mensaje, linea))

    def leer(self) -> ResultadoBC3:
        if not self.datos.strip():
            self.error("El fichero está vacío.")
            return self.r
        texto = self._decodificar()
        registros = self._registros(texto)
        if not registros:
            self.error("No es un fichero BC3: no tiene ningún registro que empiece por «~».")
            return self.r
        self._leer_registros(registros)
        self._montar()
        # Por línea; las del fichero entero (sin línea), al final
        self.r.incidencias.sort(key=lambda i: (i.linea is None, i.linea or 0))
        return self.r

    # Juego de caracteres ------------------------------------------------------

    def _decodificar(self) -> str:
        # La cabecera ~V es ASCII: se busca leyendo byte a byte (latin-1) antes de saber el juego
        crudos, _ = self._trocear(self.datos.decode("latin-1"))
        juego = ""
        for crudo in crudos:
            if crudo.tipo.strip().upper() == "V":
                juego = _campo(crudo.resto.split("|"), 4).strip().upper()
                break
        self.r.juego_de_caracteres = juego
        if juego and juego not in JUEGOS_DE_CARACTERES:
            self.aviso(
                f"Juego de caracteres «{juego}» desconocido: se lee como "
                f"{CODIFICACION_POR_DEFECTO} (ANSI)."
            )
        codificacion = JUEGOS_DE_CARACTERES.get(juego, CODIFICACION_POR_DEFECTO)
        self.r.codificacion = codificacion
        texto = self.datos.decode(codificacion, errors="replace")
        if "�" in texto:
            linea = texto.count("\n", 0, texto.index("�")) + 1
            self.aviso(
                f"Hay caracteres que no existen en {codificacion}: se muestran como «�». "
                "Comprueba el juego de caracteres del fichero.",
                linea,
            )
        return texto

    # Registros ----------------------------------------------------------------

    def _linea(self, posicion):
        return bisect.bisect_right(self._saltos, posicion - 1) + 1

    def _trocear(self, texto):
        """Parte el texto en registros (sin validar), con la línea donde empieza cada uno."""
        self._saltos = [m.start() for m in re.finditer("\n", texto)]
        inicios = [m.start() for m in re.finditer("~", texto)]
        registros = []
        for n, inicio in enumerate(inicios):
            fin = inicios[n + 1] if n + 1 < len(inicios) else len(texto)
            contenido = texto[inicio + 1 : fin]
            tipo, _, resto = contenido.partition("|")
            registros.append((tipo, resto, contenido, self._linea(inicio)))
        previo = texto[: inicios[0]] if inicios else texto
        return [_RegistroCrudo(*r) for r in registros], previo

    def _registros(self, texto):
        crudos, previo = self._trocear(texto)
        if crudos and previo.strip():
            self.aviso("Texto antes del primer registro: se ignora.", 1)
        registros = []
        for crudo in crudos:
            tipo = crudo.tipo.strip().upper()
            if "|" not in crudo.contenido or not tipo:
                muestra = crudo.contenido.strip()[:40]
                self.error(
                    f"Registro mal formado (sin campos separados por «|»): «~{muestra}».",
                    crudo.linea,
                )
                continue
            resto = crudo.resto
            if tipo != "T":
                resto = resto.replace("\r", "").replace("\n", "")
            campos = resto.split("|")
            if len(campos) > 1:
                campos = campos[:-1]  # lo que queda tras el último «|» no es un campo
            registros.append(_Registro(tipo, campos, crudo.linea))
        return registros

    def _leer_registros(self, registros):
        ignorados: dict[str, list[int]] = {}
        if registros[0].tipo != "V":
            self.aviso("La cabecera ~V no es el primer registro.", registros[0].linea)
        if not any(r.tipo == "V" for r in registros):
            self.aviso("Falta la cabecera ~V: no se sabe la versión ni el juego de caracteres.")
        for registro in registros:
            lector = getattr(self, f"_registro_{registro.tipo.lower()}", None)
            if registro.tipo in REGISTROS_LEIDOS and lector:
                lector(registro)
            else:
                ignorados.setdefault(registro.tipo, []).append(registro.linea)
        for tipo, lineas in ignorados.items():
            veces = len(lineas)
            self.aviso(
                f"Registros ~{tipo} ignorados ({veces}): esta lectura no los usa.", lineas[0]
            )

    # Números y fechas --------------------------------------------------------

    def _decimal(self, texto, linea, que) -> Decimal | None:
        texto = texto.strip()
        if not texto:
            return None
        coma = "," in texto and "." not in texto
        try:
            valor = Decimal(texto.replace(",", ".") if coma else texto)
        except InvalidOperation:
            valor = None
        if valor is None or not valor.is_finite():
            self.error(f"{que}: «{texto}» no es un número.", linea)
            return None
        if coma:
            self.aviso(f"{que}: «{texto}» usa coma decimal; se lee como {valor}.", linea)
        return valor

    def _entero(self, texto, linea, que) -> int | None:
        texto = texto.strip()
        if not texto:
            return None
        if not texto.isdigit():
            self.error(f"{que}: «{texto}» no es un número entero.", linea)
            return None
        return int(texto)

    def _fecha(self, texto, linea, que) -> date | None:
        texto = texto.strip()
        if not texto:
            return None
        try:
            if not texto.isdigit():
                raise ValueError
            if len(texto) == 8:  # DDMMAAAA
                return date(int(texto[4:]), int(texto[2:4]), int(texto[:2]))
            if len(texto) == 6:  # DDMMAA
                return date(_siglo(int(texto[4:])), int(texto[2:4]), int(texto[:2]))
            if len(texto) == 4:  # MMAA
                return date(_siglo(int(texto[2:])), int(texto[:2]), 1)
            raise ValueError
        except ValueError:
            self.aviso(
                f"{que}: fecha «{texto}» no válida (se espera DDMMAAAA); se deja vacía.", linea
            )
            return None

    # ~V ------------------------------------------------------------------------

    def _registro_v(self, reg):
        c = reg.campos
        r = self.r
        r.propietario = _campo(c, 0).strip()
        version, _, fecha = _campo(c, 1).partition("\\")
        r.version = version.strip().upper()
        r.fecha_version = self._fecha(fecha.strip("\\"), reg.linea, "Fecha de la versión")
        r.programa = _campo(c, 2).strip()
        cabecera = _subcampos(_campo(c, 3))
        r.cabecera = cabecera[0].strip() if cabecera else ""
        r.rotulos = [s.strip() for s in cabecera[1:] if s.strip()]
        r.comentario = _campo(c, 5).strip()
        r.tipo_informacion = _campo(c, 6).strip()
        if not r.version:
            self.aviso("La cabecera ~V no indica la versión del formato.", reg.linea)
        elif r.version not in VERSIONES:
            admitidas = ", ".join(v.split("/")[1] for v in VERSIONES)
            self.aviso(
                f"Versión «{r.version}» fuera de las admitidas ({admitidas}): se lee igualmente.",
                reg.linea,
            )
        if r.tipo_informacion and r.tipo_informacion not in TIPOS_INFORMACION:
            self.aviso(f"Tipo de información «{r.tipo_informacion}» desconocido.", reg.linea)

    # ~K ------------------------------------------------------------------------

    def _registro_k(self, reg):
        c, d = reg.campos, self.r.decimales
        d.campo_1, divisa_1 = self._decimales_k(_campo(c, 0), DECIMALES_CAMPO_1, reg.linea)
        d.campo_3, divisa_3 = self._decimales_k(_campo(c, 2), DECIMALES_CAMPO_3, reg.linea)
        d.divisa = divisa_3 or divisa_1
        for nombre, valor in zip(PORCENTAJES_K, _subcampos(_campo(c, 1)), strict=False):
            numero = self._decimal(valor, reg.linea, f"~K {nombre}")
            if numero is not None:
                d.porcentajes[nombre] = numero

    def _decimales_k(self, texto, nombres, linea):
        valores = _subcampos(texto)
        decimales = {}
        for nombre, valor in zip(nombres, valores, strict=False):
            numero = self._entero(valor, linea, f"~K {nombre}")
            if numero is not None:
                decimales[nombre] = numero
        divisa = valores[len(nombres)].strip() if len(valores) > len(nombres) else ""
        return decimales, divisa

    # ~C ------------------------------------------------------------------------

    def _registro_c(self, reg):
        c = reg.campos
        codigos = [s.strip() for s in _subcampos(_campo(c, 0)) if s.strip()]
        if not codigos:
            self.error("Concepto ~C sin código: se ignora.", reg.linea)
            return
        codigo = codigos[0]
        precios = []
        for texto in _subcampos(_campo(c, 3)):
            precio = self._decimal(texto, reg.linea, f"Precio de {codigo}")
            if precio is not None:
                precios.append(precio)
        fechas = [s for s in _subcampos(_campo(c, 4)) if s.strip()]
        fecha = self._fecha(fechas[0], reg.linea, f"Fecha de {codigo}") if fechas else None
        tipo = _campo(c, 5).strip() or "0"
        if tipo not in TIPOS_CONCEPTO:
            self.aviso(f"Tipo «{tipo}» de {codigo} desconocido.", reg.linea)
        if codigo in self.r.conceptos:
            anterior = self.r.conceptos[codigo].linea
            self.aviso(
                f"El concepto {codigo} se repite (antes en la línea {anterior}): vale el último.",
                reg.linea,
            )
        texto_previo = self.r.conceptos[codigo].texto if codigo in self.r.conceptos else ""
        self.r.conceptos[codigo] = Concepto(
            codigo=codigo,
            unidad=_campo(c, 1).strip(),
            resumen=_campo(c, 2).strip(),
            precio=precios[0] if precios else None,
            precios=precios,
            fecha=fecha,
            tipo=tipo,
            texto=texto_previo,
            sinonimos=codigos[1:],
            linea=reg.linea,
        )

    # ~D ------------------------------------------------------------------------

    def _registro_d(self, reg):
        c = reg.campos
        padre = _campo(c, 0).strip()
        if not padre:
            self.error("Descomposición ~D sin código de padre: se ignora.", reg.linea)
            return
        # El campo 3 (con códigos de porcentaje) sustituye al 2 cuando viene relleno
        if _campo(c, 2).strip():
            grupos, ancho = _grupos(_campo(c, 2), 4), 4
        else:
            grupos, ancho = _grupos(_campo(c, 1), 3), 3
        if grupos is None:
            self.aviso(
                f"Descomposición de {padre} incompleta: faltan subcampos al final.", reg.linea
            )
            grupos = _grupos(_campo(c, 2) if ancho == 4 else _campo(c, 1), ancho, rellenar=True)
        componentes = []
        for grupo in grupos:
            hijo = grupo[0].strip()
            if not hijo:
                self.error(
                    f"Línea de la descomposición de {padre} sin código: se ignora.", reg.linea
                )
                continue
            factor = self._decimal(grupo[1], reg.linea, f"Factor de {hijo} en {padre}")
            rendimiento = self._decimal(grupo[2], reg.linea, f"Rendimiento de {hijo} en {padre}")
            if (grupo[1].strip() and factor is None) or (grupo[2].strip() and rendimiento is None):
                continue  # número no válido: ya está el error
            porcentajes = [p for p in grupo[3].split(";") if p.strip()] if ancho == 4 else []
            componentes.append(
                Componente(
                    hijo,
                    Decimal("1") if factor is None else factor,
                    Decimal("1") if rendimiento is None else rendimiento,
                    porcentajes,
                )
            )
        if padre in self.r.descomposiciones:
            self.aviso(f"La descomposición de {padre} se repite: vale la última.", reg.linea)
        self.r.descomposiciones[padre] = componentes
        self._lineas_d[padre] = reg.linea

    # ~T ------------------------------------------------------------------------

    def _registro_t(self, reg):
        codigo = _campo(reg.campos, 0).strip()
        if not codigo:
            self.error("Texto ~T sin código de concepto: se ignora.", reg.linea)
            return
        texto = "|".join(reg.campos[1:]).replace("\r\n", "\n").replace("\r", "\n").strip()
        if codigo in self._textos:
            self.aviso(f"El texto de {codigo} se repite: vale el último.", reg.linea)
        self._textos[codigo] = (texto, reg.linea)

    # ~M ------------------------------------------------------------------------

    def _registro_m(self, reg):
        c = reg.campos
        partes = [s.strip() for s in _campo(c, 0).split("\\")]
        if len(partes) >= 2:
            padre, codigo = partes[0], partes[1]
        else:
            padre, codigo = "", partes[0] if partes else ""
        if not codigo:
            self.error("Medición ~M sin código de partida: se ignora.", reg.linea)
            return
        total = self._decimal(_campo(c, 2), reg.linea, f"Total de la medición de {codigo}")
        grupos = _grupos(_campo(c, 3), 6)
        if grupos is None:
            self.aviso(
                f"Líneas de la medición de {codigo} incompletas: faltan subcampos.", reg.linea
            )
            grupos = _grupos(_campo(c, 3), 6, rellenar=True)
        lineas = []
        for g in grupos:
            que = f"Medición de {codigo}"
            lineas.append(
                LineaMedicion(
                    tipo=g[0].strip(),
                    comentario=g[1].strip(),
                    unidades=self._decimal(g[2], reg.linea, f"{que}, unidades"),
                    longitud=self._decimal(g[3], reg.linea, f"{que}, longitud"),
                    latitud=self._decimal(g[4], reg.linea, f"{que}, latitud"),
                    altura=self._decimal(g[5], reg.linea, f"{que}, altura"),
                )
            )
        calculado = self._total_medicion(codigo, lineas, reg.linea)
        if total is not None and calculado is not None:
            exp = Decimal(1).scaleb(-self.r.decimales.medicion_total)
            if total.quantize(exp, ROUND_HALF_UP) != calculado:
                self.aviso(
                    f"La medición de {codigo} dice {total} y sus líneas suman {calculado}: "
                    "se toma la del fichero.",
                    reg.linea,
                )
        self.r.mediciones.append(
            Medicion(
                padre=padre,
                codigo=codigo,
                total=total if total is not None else calculado,
                total_calculado=calculado,
                lineas=lineas,
                posicion=[p.strip() for p in _subcampos(_campo(c, 1)) if p.strip()],
                etiqueta=_campo(c, 4).strip(),
                linea=reg.linea,
            )
        )

    def _total_medicion(self, codigo, lineas, linea):
        """Suma de las líneas normales, redondeada con los decimales DS de ~K. None si hay
        expresiones (no se evalúan) o no hay ninguna línea con cifras."""
        tipos = {LINEA_NORMAL, SUBTOTAL_PARCIAL, SUBTOTAL_ACUMULADO, EXPRESION}
        for desconocido in {ln.tipo for ln in lineas} - tipos:
            self.aviso(f"Medición de {codigo}: tipo de línea «{desconocido}» desconocido.", linea)
        if any(ln.tipo == EXPRESION for ln in lineas):
            self.aviso(
                f"Medición de {codigo}: las líneas con fórmula no se calculan; "
                "se toma el total del fichero.",
                linea,
            )
            return None
        parciales = [ln.parcial for ln in lineas if ln.parcial is not None]
        if not parciales:
            return None
        exp = Decimal(1).scaleb(-self.r.decimales.medicion_total)
        return sum(parciales, Decimal("0")).quantize(exp, ROUND_HALF_UP)

    # Árbol ---------------------------------------------------------------------

    def _buscar(self, codigo):
        """Concepto por su código, con o sin las almohadillas de capítulo o raíz."""
        conceptos = self.r.conceptos
        for candidato in (codigo, f"{codigo}#", f"{codigo}##", codigo.rstrip("#")):
            if candidato in conceptos:
                return conceptos[candidato]
        return None

    def _montar(self):
        r = self.r
        for codigo, (texto, linea) in self._textos.items():
            concepto = self._buscar(codigo)
            if concepto is None:
                self.aviso(f"Texto ~T de {codigo}, que no está definido en ningún ~C.", linea)
            else:
                concepto.texto = texto
        for padre in r.descomposiciones:
            if self._buscar(padre) is None:
                self.aviso(
                    f"Descomposición de {padre}, que no está definido en ningún ~C.",
                    self._lineas_d.get(padre),
                )

        raices = [c for c in r.conceptos.values() if c.es_raiz]
        if len(raices) > 1:
            otras = ", ".join(c.codigo for c in raices[1:])
            self.aviso(f"Hay varias raíces (##): se usa {raices[0].codigo}; se ignoran {otras}.")
        if raices:
            r.raiz = Nodo(raices[0], "raiz")
            self._colgar(r.raiz, {raices[0].codigo})
        else:
            self.aviso(
                "No hay concepto raíz (código acabado en ##): el árbol se monta desde "
                "los capítulos que no cuelgan de ningún otro."
            )
            hijos = {self._clave(h.codigo) for lista in r.descomposiciones.values() for h in lista}
            for concepto in r.conceptos.values():
                if concepto.es_capitulo and concepto.codigo not in hijos:
                    nodo = Nodo(concepto, "capitulo", nivel=1)
                    r.capitulos.append(nodo)
                    self._colgar(nodo, {concepto.codigo})
        self._enlazar_mediciones()
        if r.raiz is not None:
            en_arbol = {r.raiz.codigo}
            for nodo in r.capitulos + r.partidas + r.lineas_descompuestas:
                en_arbol.add(nodo.codigo)
            sueltos = len(r.conceptos) - len(en_arbol)
            if sueltos:
                self.aviso(f"{sueltos} concepto(s) no cuelgan del árbol de la raíz.")

    def _clave(self, codigo):
        concepto = self._buscar(codigo)
        return concepto.codigo if concepto else codigo

    def _colgar(self, nodo, camino):
        """Cuelga del nodo sus hijos según ~D, en profundidad. `camino`: códigos desde la raíz,
        para detectar descomposiciones circulares."""
        r = self.r
        if nodo.nivel >= MAX_NIVELES:
            self.error(
                f"El árbol pasa de {MAX_NIVELES} niveles en {nodo.codigo}: no se sigue bajando.",
                self._linea_d(nodo.codigo),
            )
            return
        componentes = r.descomposiciones.get(nodo.codigo)
        if componentes is None and nodo.codigo.rstrip("#") in r.descomposiciones:
            componentes = r.descomposiciones[nodo.codigo.rstrip("#")]
        for comp in componentes or []:
            hijo = self._buscar(comp.codigo)
            if hijo is None:
                self.aviso(
                    f"{comp.codigo}, en la descomposición de {nodo.codigo}, no está "
                    "definido en ningún ~C.",
                    self._linea_d(nodo.codigo),
                )
                continue
            if hijo.codigo in camino:
                self.error(
                    f"Descomposición circular: {hijo.codigo} se contiene a sí mismo "
                    f"(a través de {nodo.codigo}).",
                    self._linea_d(nodo.codigo),
                )
                continue
            if hijo.es_raiz:
                self.error(
                    f"La raíz {hijo.codigo} aparece en la descomposición de {nodo.codigo}.",
                    self._linea_d(nodo.codigo),
                )
                continue
            if hijo.es_capitulo:
                clase = "capitulo"
                if nodo.clase not in ("raiz", "capitulo"):
                    self.aviso(
                        f"El capítulo {hijo.codigo} cuelga de {nodo.codigo}, que no es "
                        "un capítulo.",
                        self._linea_d(nodo.codigo),
                    )
            elif nodo.clase in ("raiz", "capitulo"):
                clase = "partida"
            else:
                clase = "descompuesto"
            nuevo = Nodo(hijo, clase, comp.factor, comp.rendimiento, nodo.nivel + 1, nodo)
            nodo.hijos.append(nuevo)
            {"capitulo": r.capitulos, "partida": r.partidas}.get(
                clase, r.lineas_descompuestas
            ).append(nuevo)
            self._colgar(nuevo, camino | {hijo.codigo})

    def _linea_d(self, codigo):
        return self._lineas_d.get(codigo, self._lineas_d.get(codigo.rstrip("#")))

    def _enlazar_mediciones(self):
        """Cada medición a su partida en el árbol (por padre e hijo)."""
        por_codigo: dict[str, list[Nodo]] = {}
        for nodo in self.r.partidas:
            por_codigo.setdefault(nodo.codigo, []).append(nodo)
        for medicion in self.r.mediciones:
            candidatos = por_codigo.get(self._clave(medicion.codigo), [])
            if medicion.padre:
                padre = self._clave(medicion.padre)
                candidatos = [n for n in candidatos if n.padre and n.padre.codigo == padre]
            if not candidatos:
                self.aviso(
                    f"Medición de {medicion.codigo} sin partida en el árbol"
                    + (f" dentro de {medicion.padre}." if medicion.padre else "."),
                    medicion.linea,
                )
                continue
            for nodo in candidatos[:1] if not medicion.padre else candidatos:
                nodo.medicion = medicion


# ── Ayudas ─────────────────────────────────────────────────────────────────────


@dataclass
class _RegistroCrudo:
    tipo: str
    resto: str
    contenido: str
    linea: int


def _campo(campos, indice):
    return campos[indice] if indice < len(campos) else ""


def _subcampos(texto):
    """Subcampos separados por «\\», sin el vacío que deja la barra final."""
    if not texto.strip():
        return []
    partes = texto.split("\\")
    if partes and not partes[-1].strip():
        partes = partes[:-1]
    return partes


def _grupos(texto, ancho, rellenar=False):
    """Subcampos en grupos de `ancho` (p. ej. hijo\\factor\\rendimiento). None si no cuadran
    y no se pide `rellenar` (que completa el último grupo con vacíos)."""
    if not texto.strip():
        return []
    partes = texto.split("\\")
    if len(partes) % ancho == 1 and not partes[-1].strip():
        partes = partes[:-1]
    if len(partes) % ancho:
        if not rellenar:
            return None
        partes += [""] * (ancho - len(partes) % ancho)
    return [partes[i : i + ancho] for i in range(0, len(partes), ancho)]


def _siglo(anyo):
    """Años de dos cifras: 00-79 → 2000-2079; 80-99 → 1980-1999."""
    return 2000 + anyo if anyo < 80 else 1900 + anyo
