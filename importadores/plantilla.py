"""Lector de la plantilla de cálculo en Excel (una hoja por partida): solo lectura, sin Django.

Uso:
    resultado = leer_plantilla("calculo.xlsm")   # ruta, o los bytes del fichero
    resultado.parametros, resultado.partidas, resultado.lecciones, resultado.avisos,
    resultado.errores

Formato fijo de cada hoja de partida (el nombre de la hoja es el de la partida):

    D6  medición                  F6  precio de mano de obra por unidad
    H6  beneficio (0,10 = 10 %)   H8  margen de departamento (0,10 = 10 %)
    C6  etiqueta de la medición («M2 Obra»): de ahí se propone la unidad
    C9  rendimiento = coste día / F6 (el coste día va escrito dentro de la fórmula)
    filas 14 a 33: material (columna B) · M consumo · N consumo con merma · G precio de tarifa ·
    I descuento
    H36 fijaciones por unidad · H37 transportes por unidad · H47 / K47 venta por unidad y total

Hojas que no son partidas: ÍNDICE (parámetros generales en F6:F9), Hoja1 y Hoja2 (marcan las
partidas activas: las que quedan entre las dos), RESUMEN, RESUMEN PARTIDAS y MATERIAL.

Se leen los valores calculados que guardó Excel y, aparte, las fórmulas, para detectar lo que
la plantilla hace mal (`lecciones`). No se ejecutan macros. Nunca lanza excepciones por el
contenido: lo que no se puede leer va a `errores` y lo dudoso a `avisos`.
"""

from __future__ import annotations

import io
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

HOJAS_AUXILIARES = {"INDICE", "HOJA1", "HOJA2", "RESUMEN", "RESUMEN PARTIDAS", "MATERIAL"}
FILAS_MATERIAL = range(14, 34)
FILA_TITULOS = 12  # «MATERIALES» en la columna B
COSTE_DIA_PLANTILLA = Decimal("189")  # el que lleva escrito la plantilla original
PARAMETROS_INDICE = {
    "F6": ("distancia_km", "Distancia a obra (km, ida)"),
    "F7": ("incremento_comercial_pct", "Incremento comercial (%)"),
    "F8": ("importe_km", "Importe por km (€/km)"),
    "F9": ("operarios_desplazados", "Operarios desplazados"),
}
UNIDADES = {"M2": "m²", "M²": "m²", "M3": "m³", "M³": "m³", "ML": "ml", "M": "m", "UD": "ud",
            "U": "ud", "KG": "kg", "H": "h", "PA": "pa"}  # fmt: skip
UNIDAD_POR_DEFECTO = "m²"
CIEN = Decimal("100")


def normalizar(texto: str) -> str:
    """Para comparar nombres: sin tildes, en minúsculas y con los espacios justos."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return " ".join(sin_tildes.casefold().split())


@dataclass
class Material:
    fila: int
    nombre: str
    consumo: Decimal  # por unidad de partida, sin merma
    merma_pct: Decimal
    precio: Decimal | None  # de tarifa
    descuento_pct: Decimal

    @property
    def precio_neto(self) -> Decimal | None:
        if self.precio is None:
            return None
        return self.precio * (1 - self.descuento_pct / CIEN)


@dataclass
class HojaPartida:
    nombre: str
    posicion: int  # orden de la hoja en el libro
    activa: bool  # entre Hoja1 y Hoja2
    etiqueta_unidad: str
    unidad: str  # propuesta desde la etiqueta
    medicion: Decimal | None
    precio_mo: Decimal | None  # por unidad
    beneficio_pct: Decimal | None
    coste_dia: Decimal | None  # el de la fórmula del rendimiento
    materiales: list = field(default_factory=list)
    fijaciones: Decimal = Decimal("0")
    transportes: Decimal = Decimal("0")
    venta_ud: Decimal | None = None  # H47, con gastos de empresa y beneficio
    total: Decimal | None = None  # K47

    @property
    def coste_directo_plantilla(self) -> Decimal:
        """Materiales + fijaciones + transportes + mano de obra, como la plantilla (H34..H38)."""
        importes = (m.consumo * (1 + m.merma_pct / CIEN) * (m.precio_neto or 0)
                    for m in self.materiales)  # fmt: skip
        materiales = sum(importes, Decimal("0")).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return materiales + self.fijaciones + self.transportes + (self.precio_mo or 0)


@dataclass
class Leccion:
    numero: int  # el de la lista de lecciones de la plantilla
    texto: str


@dataclass
class ResultadoPlantilla:
    parametros: dict = field(default_factory=dict)  # clave → (etiqueta, valor)
    partidas: list = field(default_factory=list)
    lecciones: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    errores: list = field(default_factory=list)
    hojas_ignoradas: list = field(default_factory=list)

    @property
    def coste_dia(self) -> Decimal | None:
        """El coste día de la plantilla: el que más se repite en sus hojas."""
        valores = [p.coste_dia for p in self.partidas if p.coste_dia is not None]
        return max(set(valores), key=valores.count) if valores else None

    @property
    def total_hojas(self) -> Decimal:
        """Total de las partidas activas, calculado desde sus hojas (nunca desde los resúmenes)."""
        return sum((p.total or 0 for p in self.partidas if p.activa), Decimal("0"))


# ── Lectura de celdas ──────────────────────────────────────────────────────────


def _numero(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return Decimal(repr(valor)) if isinstance(valor, float) else Decimal(valor)
    if isinstance(valor, str):
        texto = valor.strip().replace("€", "").replace("%", "").strip()
        if not texto:
            return None
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            return Decimal(texto)
        except InvalidOperation:
            return None
    return None


def _pct(fraccion: Decimal | None) -> Decimal | None:
    """0,10 → 10,00 (los porcentajes de la plantilla son fracciones)."""
    return None if fraccion is None else (fraccion * CIEN).quantize(Decimal("0.01"))


def _texto(valor) -> str:
    return " ".join(str(valor).split()) if valor is not None else ""


def _formula(celda) -> str:
    valor = celda.value
    if isinstance(valor, str) and valor.startswith("="):
        return valor.replace(" ", "").upper()
    return ""


def unidad_de_etiqueta(etiqueta: str) -> str:
    """«M2 Obra» → «m²». Si no se reconoce, m²."""
    primera = (etiqueta or "").split()
    if not primera:
        return UNIDAD_POR_DEFECTO
    return UNIDADES.get(primera[0].upper().rstrip("."), UNIDAD_POR_DEFECTO)


_COSTE_DIA = re.compile(r"^=\+?\(?([0-9]+(?:[.,][0-9]+)?)\)?/\$?F\$?6$")


def _coste_dia(valor, formula, precio_mo) -> Decimal | None:
    """De «=189/F6» sale 189. Si la celda es un número (sin fórmula), rendimiento × F6."""
    encontrado = _COSTE_DIA.match(formula) if formula else None
    if encontrado:
        return _numero(encontrado.group(1).replace(",", "."))
    rendimiento = _numero(valor)
    if not formula and rendimiento and precio_mo:
        return (rendimiento * precio_mo).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return None


def _es_hoja_de_partida(valores) -> bool:
    return _texto(valores[f"B{FILA_TITULOS}"].value).upper() == "MATERIALES" or (
        _numero(valores["D6"].value) is not None
        and _texto(valores["G38"].value).upper() == "MANO DE OBRA"
    )


def _leer_partida(nombre, posicion, activa, valores, formulas, r: ResultadoPlantilla):
    def aviso(texto):
        r.avisos.append(f"{nombre}: {texto}")

    medicion = _numero(valores["D6"].value)
    precio_mo = _numero(valores["F6"].value)
    beneficio = _numero(valores["H6"].value)
    etiqueta = _texto(valores["C6"].value)
    hoja = HojaPartida(
        nombre=nombre,
        posicion=posicion,
        activa=activa,
        etiqueta_unidad=etiqueta,
        unidad=unidad_de_etiqueta(etiqueta),
        medicion=medicion,
        precio_mo=precio_mo,
        beneficio_pct=_pct(beneficio),
        coste_dia=_coste_dia(valores["C9"].value, _formula(formulas["C9"]), precio_mo),
        fijaciones=_numero(valores["H36"].value) or Decimal("0"),
        transportes=_numero(valores["H37"].value) or Decimal("0"),
        venta_ud=_numero(valores["H47"].value),
        total=_numero(valores["K47"].value),
    )
    if precio_mo is None:
        aviso("sin precio de mano de obra por unidad (F6): la partida no llevará mano de obra.")
    for fila in FILAS_MATERIAL:
        material = _texto(valores[f"B{fila}"].value)
        consumo = _numero(valores[f"M{fila}"].value)
        if not material and not consumo:
            continue
        if not material:
            aviso(f"fila {fila} con consumo y sin nombre de material: no se importa.")
            continue
        if not consumo:
            aviso(f"«{material}» (fila {fila}) sin consumo: no se importa.")
            continue
        con_merma = _numero(valores[f"N{fila}"].value)
        if con_merma is None:
            merma = Decimal("10")
            aviso(f"«{material}» sin consumo con merma (N{fila}): se toma el 10 %.")
        else:
            merma = ((con_merma / consumo - 1) * CIEN).quantize(Decimal("0.01"), ROUND_HALF_UP)
        descuento = _numero(valores[f"I{fila}"].value) or Decimal("0")
        precio = _numero(valores[f"G{fila}"].value)
        if precio is None:
            aviso(f"«{material}» sin precio de tarifa (G{fila}).")
        hoja.materiales.append(Material(fila, material, consumo, merma, precio, descuento * CIEN))
    return hoja


# ── Lecciones: lo que la plantilla hace mal y la app corrige ───────────────────


def _filas_resumen_partidas(hoja):
    """(descripción, total) de RESUMEN PARTIDAS: columnas B y K desde la fila 6."""
    filas = []
    for fila in range(6, hoja.max_row + 1):
        descripcion = _texto(hoja[f"B{fila}"].value)
        total = _numero(hoja[f"K{fila}"].value)
        if descripcion and total is not None:
            filas.append((descripcion, total))
    return filas


def _dinero(valor: Decimal) -> str:
    entero, _, decimales = f"{valor.quantize(Decimal('0.01'), ROUND_HALF_UP):,.2f}".partition(".")
    return f"{entero.replace(',', '.')},{decimales} €"


def _lecciones(libro_v, libro_f, r: ResultadoPlantilla):
    nombres = {normalizar(n): n for n in libro_v.sheetnames}
    hojas = {normalizar(p.nombre) for p in r.partidas}

    # 1. Resúmenes que son copias (RESUMEN PARTIDAS lo rellena una macro y puede duplicar filas)
    if "resumen partidas" in nombres:
        filas = _filas_resumen_partidas(libro_v[nombres["resumen partidas"]])
        sueltas = [d for d, _ in filas if normalizar(d) not in hojas]
        suma = sum((t for _, t in filas), Decimal("0"))
        if sueltas or (filas and suma != r.total_hojas):
            texto = (
                f"RESUMEN PARTIDAS suma {_dinero(suma)} y las hojas de las partidas activas, "
                f"{_dinero(r.total_hojas)}."
            )
            if sueltas:
                texto += " Filas sin hoja: " + ", ".join(f"«{d}»" for d in sueltas) + "."
            r.lecciones.append(
                Leccion(1, texto + " Los resúmenes no se importan: los totales se calculan "
                        "siempre desde las partidas.")
            )  # fmt: skip
    if "resumen" in nombres:
        total = _numero(libro_v[nombres["resumen"]]["F15"].value)
        if total is not None and total != r.total_hojas:
            r.lecciones.append(
                Leccion(1, f"RESUMEN da {_dinero(total)} y las hojas, {_dinero(r.total_hojas)}.")
            )

    # 2, 3 y 6: fórmulas de las hojas de partida
    financiero, gastos, coste_dia, divisores = [], [], [], []
    for p in r.partidas:
        f = libro_f[p.nombre]
        h41, j41 = _formula(f["H41"]), _formula(f["J41"])
        if h41 and j41 and ("F8" in h41) != ("F8" in j41):
            financiero.append(p.nombre)
        if any("0.14" in _formula(f[c]) for c in ("H39", "J39")):
            gastos.append(p.nombre)
        if _COSTE_DIA.match(_formula(f["C9"])):
            coste_dia.append(p.nombre)
        if any(re.search(r"/\(?\d+\)?$", _formula(f[f"F{n}"])) for n in range(53, 61)):
            divisores.append(p.nombre)
    if financiero:
        r.lecciones.append(
            Leccion(2, f"El gasto financiero usa una celda en una columna (F8) y otra en la otra "
                    f"(H8, «% Obras») en {len(financiero)} hoja(s). No se importa: en la app "
                    "cada porcentaje es un campo con nombre.")
        )  # fmt: skip
    if gastos:
        r.lecciones.append(
            Leccion(3, f"Gastos de empresa del 14 % escritos dentro de la fórmula en {len(gastos)} "
                    "hoja(s): no se importan; la estructura va en el coste hora.")
        )  # fmt: skip
    if coste_dia:
        valor = r.coste_dia or COSTE_DIA_PLANTILLA
        r.lecciones.append(
            Leccion(3, f"Coste día de {valor:f} € escrito dentro de la fórmula del rendimiento en "
                    f"{len(coste_dia)} hoja(s): aquí es un parámetro visible.")
        )  # fmt: skip

    # 4. Desplazamiento: indirecto en RESUMEN y directo en RESUMEN PARTIDAS
    if "resumen" in nombres and "resumen partidas" in nombres:
        resumen = libro_v[nombres["resumen"]]
        etiquetas = [normalizar(_texto(resumen[f"A{n}"].value)) for n in range(1, 40)]
        cabecera = [normalizar(_texto(c.value)) for c in libro_v[nombres["resumen partidas"]][5]]
        if "coste directo" in etiquetas and "coste indirecto" in etiquetas:
            directo = etiquetas.index("coste directo")
            indirecto = etiquetas.index("coste indirecto")
            en_indirecto = any(
                e.startswith("desplazamiento") for e in etiquetas[directo + 1 : indirecto]
            )
            en_directo = any(c.startswith("desplaz") for c in cabecera[: _posicion(cabecera)])
            if en_indirecto and en_directo:
                r.lecciones.append(
                    Leccion(4, "El desplazamiento es coste indirecto en RESUMEN y directo en "
                            "RESUMEN PARTIDAS. No se importa: lo calcula el presupuesto.")
                )  # fmt: skip
    if divisores:
        r.lecciones.append(
            Leccion(6, f"Conversión a sacos o rollos con divisores fijos en {len(divisores)} "
                    "hoja(s): no se importa; la unidad de compra irá en el artículo.")
        )  # fmt: skip

    # 8. Partidas activas por su posición entre Hoja1 y Hoja2
    if "hoja1" in nombres and "hoja2" in nombres:
        activas = sum(p.activa for p in r.partidas)
        r.lecciones.append(
            Leccion(8, f"{activas} hoja(s) entre Hoja1 y Hoja2 (activas en la plantilla) y "
                    f"{len(r.partidas) - activas} de biblioteca: se importan todas igual.")
        )  # fmt: skip


def _posicion(cabecera):
    return cabecera.index("c. directo") if "c. directo" in cabecera else len(cabecera)


# ── Entrada ────────────────────────────────────────────────────────────────────


def leer_plantilla(origen: bytes | str | Path) -> ResultadoPlantilla:
    """Lee la plantilla de cálculo (.xlsx o .xlsm) y devuelve sus partidas, sin guardar nada."""
    import openpyxl

    r = ResultadoPlantilla()
    datos = Path(origen).read_bytes() if isinstance(origen, (str, Path)) else origen
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # cabeceras y pies de página que no entiende
            libro_v = openpyxl.load_workbook(io.BytesIO(datos), data_only=True)
            libro_f = openpyxl.load_workbook(io.BytesIO(datos), data_only=False)
    except Exception:  # noqa: BLE001 — cualquier fallo de formato es «no es un Excel»
        r.errores.append("No es un libro de Excel válido (.xlsx o .xlsm).")
        return r

    nombres = libro_v.sheetnames
    claves = [normalizar(n).upper() for n in nombres]
    if "INDICE" in claves:
        indice = libro_v[nombres[claves.index("INDICE")]]
        for celda, (clave, etiqueta) in PARAMETROS_INDICE.items():
            valor = _numero(indice[celda].value)
            if valor is not None:
                r.parametros[clave] = (etiqueta, valor)
    else:
        r.avisos.append("No hay hoja ÍNDICE: sin parámetros generales.")

    dentro = False
    for posicion, nombre in enumerate(nombres):
        clave = claves[posicion]
        if clave in ("HOJA1", "HOJA2"):
            dentro = clave == "HOJA1"
        if clave in HOJAS_AUXILIARES:
            continue
        valores = libro_v[nombre]
        if not _es_hoja_de_partida(valores):
            r.hojas_ignoradas.append(nombre)
            r.avisos.append(f"La hoja «{nombre}» no tiene la forma de una partida: no se importa.")
            continue
        r.partidas.append(_leer_partida(nombre, posicion, dentro, valores, libro_f[nombre], r))
    if not r.partidas:
        r.errores.append("El libro no tiene hojas de partida.")
    _lecciones(libro_v, libro_f, r)
    return r
