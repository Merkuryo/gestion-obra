"""Lógica del banco de precios. Las vistas llaman aquí; nunca escriben en el banco por su cuenta.

- Importación de un BC3 como **banco externo**: vista previa sin guardar (`preparar_bc3`) y
  confirmación (`importar_bc3`). Es idempotente: el mismo banco (misma raíz y propietario) se
  actualiza por código; lo que ya no viene en el fichero se borra del banco externo.
- **Copia** de una partida de un banco externo al **banco propio** (`copiar_al_banco_propio`):
  se mantienen los rendimientos; los recursos se copian marcando lo que falta para que su precio
  sea el de la empresa (mano de obra → coste hora; material → artículo y tarifa).
- **Precio de las partidas del banco propio:** se recalcula desde su descompuesto cada vez que
  cambia una línea o el precio de un recurso (catalogos/calculos.py).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from importadores.bc3 import ResultadoBC3, leer_bc3

from . import calculos
from .models import ORIGEN_PLANTILLA, Banco, Capitulo, LineaDescompuesto, Partida, Recurso

TIPOS_BC3 = {"1": Recurso.MANO_OBRA, "2": Recurso.MAQUINARIA, "3": Recurso.MATERIAL}
LARGO = {"codigo": 40, "unidad": 20, "resumen": 255}


def banco_propio() -> Banco:
    banco, _ = Banco.objects.get_or_create(tipo=Banco.PROPIO, defaults={"nombre": "Banco propio"})
    return banco


# ── Coste hora (mano de obra) ──────────────────────────────────────────────────


def coste_hora_actual():
    """Coste hora en tiempo real de la empresa, para el precio de la mano de obra.

    Todavía no existe: llega con la estructura de costes (personal y gastos). Hasta entonces
    devuelve None y la mano de obra copiada de un banco externo conserva su precio de origen,
    marcada como «Recalcular con el coste hora» (Recurso.pendiente). Cuando exista, este es el
    único punto que hay que cambiar: `copiar_al_banco_propio` ya lo usa.
    """
    return None


# ── Importación BC3: clasificación ─────────────────────────────────────────────


@dataclass
class ContenidoBC3:
    """Lo que se guardaría de un BC3, ya clasificado."""

    capitulos: list = field(default_factory=list)  # (codigo, resumen, codigo_padre)
    partidas: dict = field(default_factory=dict)  # codigo → (Concepto, codigo_capitulo, auxiliar)
    recursos: dict = field(default_factory=dict)  # codigo → Concepto
    lineas: dict = field(default_factory=dict)  # codigo_partida → [(codigo, factor, rendimiento)]
    recortes: int = 0  # textos más largos de lo que admite el campo
    redondeos: int = 0  # números con más de 4 decimales

    @property
    def num_lineas(self):
        return sum(len(v) for v in self.lineas.values())

    @property
    def num_auxiliares(self):
        return sum(1 for _, _, auxiliar in self.partidas.values() if auxiliar)


def _concepto(resultado, codigo):
    conceptos = resultado.conceptos
    for candidato in (codigo, f"{codigo}#", f"{codigo}##", codigo.rstrip("#")):
        if candidato in conceptos:
            return conceptos[candidato]
    return None


def _componentes(resultado, codigo):
    d = resultado.descomposiciones
    return d.get(codigo) or d.get(codigo.rstrip("#")) or []


def clasificar(resultado: ResultadoBC3) -> ContenidoBC3:
    """Capítulos, partidas (y auxiliares), recursos y líneas de descompuesto de un BC3 leído."""
    c = ContenidoBC3()
    vistos = set()
    for nodo in resultado.capitulos:
        if nodo.codigo in vistos:
            continue
        vistos.add(nodo.codigo)
        padre = nodo.padre.codigo if nodo.padre and nodo.padre.clase == "capitulo" else None
        c.capitulos.append((nodo.codigo, nodo.concepto.resumen, padre))

    con_descompuesto = {
        con.codigo
        for padre in resultado.descomposiciones
        if (con := _concepto(resultado, padre)) and not (con.es_capitulo or con.es_raiz)
    }
    for nodo in resultado.partidas:
        if nodo.codigo not in c.partidas:
            capitulo = nodo.padre.codigo if nodo.padre and nodo.padre.clase == "capitulo" else None
            c.partidas[nodo.codigo] = (nodo.concepto, capitulo, False)
    for nodo in resultado.lineas_descompuestas:
        codigo = nodo.codigo
        if codigo in c.partidas or codigo in c.recursos:
            continue
        if codigo in con_descompuesto:
            c.partidas[codigo] = (nodo.concepto, None, True)
        else:
            c.recursos[codigo] = nodo.concepto
    # Conceptos que no cuelgan del árbol (habitual en bases de precios): también son del banco
    for codigo, concepto in resultado.conceptos.items():
        if concepto.es_raiz or concepto.es_capitulo:
            continue
        if codigo in c.partidas or codigo in c.recursos:
            continue
        if codigo in con_descompuesto:
            c.partidas[codigo] = (concepto, None, False)
        else:
            c.recursos[codigo] = concepto

    for codigo in c.partidas:
        lineas = []
        for comp in _componentes(resultado, codigo):
            hijo = _concepto(resultado, comp.codigo)
            if hijo is None or hijo.es_capitulo or hijo.es_raiz:
                continue
            lineas.append((hijo.codigo, comp.factor, comp.rendimiento))
        if lineas:
            c.lineas[codigo] = lineas
    return c


# ── Importación BC3: vista previa y confirmación ───────────────────────────────


@dataclass
class VistaPreviaBC3:
    resultado: ResultadoBC3
    contenido: ContenidoBC3
    fichero: str
    nombre: str
    clave: str
    existente: Banco | None

    @property
    def recuento(self):
        return {
            "capitulos": len(self.contenido.capitulos),
            "partidas": len(self.contenido.partidas) - self.contenido.num_auxiliares,
            "auxiliares": self.contenido.num_auxiliares,
            "recursos": len(self.contenido.recursos),
            "lineas": self.contenido.num_lineas,
        }

    @property
    def avisos(self):
        avisos = [str(a) for a in self.resultado.avisos]
        if self.contenido.redondeos:
            avisos.append(
                f"{self.contenido.redondeos} precio(s) o rendimiento(s) con más de 4 decimales: "
                "se guardan redondeados a 4."
            )
        if self.contenido.recortes:
            avisos.append(
                f"{self.contenido.recortes} texto(s) más largos de lo admitido: se recortan."
            )
        return avisos

    @property
    def errores(self):
        return [str(e) for e in self.resultado.errores]

    @property
    def se_puede_importar(self):
        return bool(self.contenido.partidas or self.contenido.recursos)


def _clave(resultado, fichero):
    raiz = resultado.raiz.codigo if resultado.raiz else ""
    if raiz:
        return f"{raiz}|{resultado.propietario}"[:255]
    return f"fichero:{fichero}"[:255]


def _nombre(resultado, fichero):
    if resultado.raiz and resultado.raiz.concepto.resumen:
        return resultado.raiz.concepto.resumen[:200]
    return (resultado.cabecera or fichero)[:200]


def _contar_redondeos(contenido):
    total = 0
    for concepto, _, _ in contenido.partidas.values():
        total += calculos.redondear_d4(concepto.precio)[1]
    for concepto in contenido.recursos.values():
        total += calculos.redondear_d4(concepto.precio)[1]
    for lineas in contenido.lineas.values():
        for _, factor, rendimiento in lineas:
            total += calculos.redondear_d4(factor)[1] + calculos.redondear_d4(rendimiento)[1]
    return total


def _contar_recortes(contenido):
    textos = [(con.codigo, con.unidad, con.resumen) for con, _, _ in contenido.partidas.values()]
    textos += [(con.codigo, con.unidad, con.resumen) for con in contenido.recursos.values()]
    textos += [(cod, "", res) for cod, res, _ in contenido.capitulos]
    limites = (LARGO["codigo"], LARGO["unidad"], LARGO["resumen"])
    return sum(any(len(t) > lim for t, lim in zip(fila, limites, strict=True)) for fila in textos)


def preparar_bc3(datos: bytes, fichero: str) -> VistaPreviaBC3:
    """Lee el BC3 y dice qué se importaría, sin guardar nada."""
    resultado = leer_bc3(datos)
    contenido = clasificar(resultado)
    contenido.redondeos = _contar_redondeos(contenido)
    contenido.recortes = _contar_recortes(contenido)
    clave = _clave(resultado, fichero)
    existente = Banco.objects.filter(tipo=Banco.EXTERNO, clave=clave).first()
    return VistaPreviaBC3(
        resultado, contenido, fichero, _nombre(resultado, fichero), clave, existente
    )


@dataclass
class ResumenImportacion:
    banco: Banco
    nuevo: bool
    creados: dict = field(default_factory=dict)
    actualizados: dict = field(default_factory=dict)
    borrados: dict = field(default_factory=dict)


def _texto(valor, campo):
    return (valor or "")[: LARGO[campo]]


def _sincronizar(modelo, banco, deseados, campos, actor, ahora):
    """Crea, actualiza y borra objetos de `modelo` en `banco` para que queden `deseados`
    ({codigo: {campo: valor}}). Devuelve ({codigo: objeto}, creados, actualizados, borrados)."""
    existentes = {o.codigo: o for o in modelo.objects.filter(banco=banco)}
    nuevos, cambiados = [], []
    for codigo, valores in deseados.items():
        objeto = existentes.get(codigo)
        if objeto is None:
            nuevos.append(
                modelo(banco=banco, codigo=codigo, creado_por=actor, modificado_por=actor,
                       **valores)
            )  # fmt: skip
            continue
        if any(getattr(objeto, k) != v for k, v in valores.items()):
            for k, v in valores.items():
                setattr(objeto, k, v)
            objeto.modificado_por, objeto.modificado_en = actor, ahora
            cambiados.append(objeto)
    modelo.objects.bulk_create(nuevos, batch_size=1000)
    if cambiados:
        modelo.objects.bulk_update(
            cambiados, [*campos, "modificado_por", "modificado_en"], batch_size=1000
        )
    sobran = [o.pk for codigo, o in existentes.items() if codigo not in deseados]
    if sobran:
        modelo.objects.filter(pk__in=sobran).delete()
    todos = {o.codigo: o for o in modelo.objects.filter(banco=banco)}
    return todos, len(nuevos), len(cambiados), len(sobran)


@transaction.atomic
def importar_bc3(datos: bytes, fichero: str, actor) -> ResumenImportacion:
    """Importa el BC3 como banco externo. Si ya hay un banco con la misma clave, lo actualiza
    por código (no duplica) y borra lo que el fichero ya no trae."""
    vista = preparar_bc3(datos, fichero)
    if not vista.se_puede_importar:
        raise ValidationError("El fichero no tiene partidas ni recursos que importar.")
    r, contenido, ahora = vista.resultado, vista.contenido, timezone.now()

    banco = Banco.objects.select_for_update().filter(tipo=Banco.EXTERNO, clave=vista.clave).first()
    nuevo = banco is None
    if nuevo:
        banco = Banco(tipo=Banco.EXTERNO, clave=vista.clave, creado_por=actor)
    banco.nombre = vista.nombre
    banco.fichero = fichero[:255]
    banco.version_formato = r.version[:30]
    banco.programa = r.programa[:200]
    banco.propietario = r.propietario[:200]
    banco.fecha_fichero = r.fecha_version
    banco.importado_en, banco.importado_por, banco.modificado_por = ahora, actor, actor
    banco._change_reason = f"Importado de {fichero}"
    banco.save()

    resumen = ResumenImportacion(banco, nuevo)

    def anotar(nombre, creados, actualizados, borrados):
        resumen.creados[nombre] = creados
        resumen.actualizados[nombre] = actualizados
        resumen.borrados[nombre] = borrados

    # Las líneas no tienen identidad propia: se rehacen enteras. Se borran sin señales para no
    # crear un registro de historial por línea (un banco externo no lleva historial por fila;
    # con decenas de miles de líneas, reimportar pasaría de segundos a minutos).
    lineas_viejas = LineaDescompuesto.objects.filter(partida__banco=banco)
    lineas_viejas._raw_delete(lineas_viejas.db)

    capitulos_deseados = {
        _texto(cod, "codigo"): {"resumen": _texto(res, "resumen"), "orden": n}
        for n, (cod, res, _) in enumerate(contenido.capitulos)
    }
    capitulos, *cuentas = _sincronizar(
        Capitulo, banco, capitulos_deseados, ["resumen", "orden"], actor, ahora
    )
    anotar("capitulos", *cuentas)
    con_padre = []
    for cod, _, padre in contenido.capitulos:
        capitulo = capitulos[_texto(cod, "codigo")]
        nuevo_padre = capitulos.get(_texto(padre, "codigo")) if padre else None
        if capitulo.padre_id != (nuevo_padre.pk if nuevo_padre else None):
            capitulo.padre = nuevo_padre
            con_padre.append(capitulo)
    Capitulo.objects.bulk_update(con_padre, ["padre"], batch_size=1000)

    recursos_deseados = {
        _texto(cod, "codigo"): {
            "tipo": TIPOS_BC3.get(con.tipo, Recurso.OTRO),
            "unidad": _texto(con.unidad, "unidad"),
            "resumen": _texto(con.resumen, "resumen") or cod,
            "texto": con.texto,
            "precio": calculos.redondear_d4(con.precio)[0],
            "fecha_precio": con.fecha,
        }
        for cod, con in contenido.recursos.items()
    }
    recursos, *cuentas = _sincronizar(
        Recurso, banco, recursos_deseados, list(next(iter(recursos_deseados.values()), {})),
        actor, ahora,
    )  # fmt: skip
    anotar("recursos", *cuentas)

    partidas_deseadas = {
        _texto(cod, "codigo"): {
            # por id: comparar el objeto haría una consulta por partida al reimportar
            "capitulo_id": capitulos[_texto(cap, "codigo")].pk if cap else None,
            "unidad": _texto(con.unidad, "unidad"),
            "resumen": _texto(con.resumen, "resumen") or cod,
            "texto": con.texto,
            "precio": calculos.redondear_d4(con.precio)[0],
            "fecha_precio": con.fecha,
            "auxiliar": auxiliar,
        }
        for cod, (con, cap, auxiliar) in contenido.partidas.items()
    }
    campos_partida = list(next(iter(partidas_deseadas.values()), {}))
    partidas, *cuentas = _sincronizar(
        Partida, banco, partidas_deseadas, campos_partida, actor, ahora
    )
    anotar("partidas", *cuentas)

    lineas = []
    for codigo, componentes in contenido.lineas.items():
        partida = partidas[_texto(codigo, "codigo")]
        for orden, (hijo, factor, rendimiento) in enumerate(componentes):
            hijo = _texto(hijo, "codigo")
            lineas.append(
                LineaDescompuesto(
                    partida=partida,
                    orden=orden,
                    recurso=recursos.get(hijo),
                    auxiliar=None if hijo in recursos else partidas.get(hijo),
                    factor=calculos.redondear_d4(factor)[0],
                    rendimiento=calculos.redondear_d4(rendimiento)[0],
                    creado_por=actor,
                    modificado_por=actor,
                )
            )
    LineaDescompuesto.objects.bulk_create(
        [ln for ln in lineas if ln.recurso or ln.auxiliar], batch_size=1000
    )
    return resumen


# ── Banco propio: precios ──────────────────────────────────────────────────────


def recalcular_precio(partida: Partida, actor=None, _hechas=None) -> Partida:
    """Recalcula el precio de una partida del banco propio desde su descompuesto (y antes el de
    sus partidas auxiliares). Solo guarda si cambia."""
    hechas = {} if _hechas is None else _hechas
    if partida.pk in hechas:
        return hechas[partida.pk]
    hechas[partida.pk] = partida
    filas = []
    for linea in partida.lineas.select_related("recurso", "auxiliar"):
        if linea.auxiliar_id:
            precio = recalcular_precio(linea.auxiliar, actor, hechas).precio
        else:
            precio = linea.recurso.precio
        filas.append((linea.factor, linea.rendimiento, linea.merma_pct, precio))
    nuevo = calculos.precio_partida(filas)
    if nuevo != partida.precio:
        partida.precio = nuevo
        partida.fecha_precio = date.today()
        if actor is not None:
            partida.modificado_por = actor
        partida._change_reason = "Precio recalculado desde el descompuesto"
        partida.save()
    return partida


def recalcular_dependientes(partidas, actor=None):
    """Recalcula las partidas indicadas y, hacia arriba, las que las usan como auxiliares."""
    pendientes, vistas = list(partidas), set()
    while pendientes:
        partida = pendientes.pop()
        if partida.pk in vistas:
            continue
        vistas.add(partida.pk)
        recalcular_precio(partida, actor)
        pendientes.extend(Partida.objects.filter(lineas__auxiliar=partida).distinct())


def _comprobar_sin_ciclos(partida: Partida):
    """Una partida no puede contenerse a sí misma a través de sus auxiliares."""
    pendientes = [ln.auxiliar for ln in partida.lineas.exclude(auxiliar=None)]
    vistas = set()
    while pendientes:
        auxiliar = pendientes.pop()
        if auxiliar.pk == partida.pk:
            raise ValidationError(
                "El descompuesto no puede incluir la propia partida (ni a través de auxiliares)."
            )
        if auxiliar.pk in vistas:
            continue
        vistas.add(auxiliar.pk)
        pendientes.extend(ln.auxiliar for ln in auxiliar.lineas.exclude(auxiliar=None))


def _registrar(objeto, actor, motivo=None):
    if objeto._state.adding:
        objeto.creado_por = actor
    objeto.modificado_por = actor
    objeto._change_reason = motivo
    objeto.save()


def _solo_propio(objeto):
    if not objeto.banco.es_propio:
        raise ValidationError("Los bancos externos son de solo lectura.")


@transaction.atomic
def guardar_partida(partida: Partida, lineas, borrar, actor) -> Partida:
    """Guarda una partida del banco propio con su descompuesto y recalcula su precio.

    `lineas`: LineaDescompuesto nuevas o cambiadas (sin guardar); `borrar`: las que se quitan.
    """
    partida.banco = partida.banco if partida.banco_id else banco_propio()
    _solo_propio(partida)
    _registrar(partida, actor)
    for linea in borrar:
        linea.delete()
    for orden, linea in enumerate(lineas):
        linea.partida = partida
        if linea.orden is None:
            linea.orden = orden
        _registrar(linea, actor)
    _comprobar_sin_ciclos(partida)
    recalcular_dependientes([partida], actor)
    partida.refresh_from_db()
    return partida


@transaction.atomic
def guardar_recurso(recurso: Recurso, actor) -> Recurso:
    """Guarda un recurso del banco propio y recalcula las partidas que lo usan."""
    recurso.banco = recurso.banco if recurso.banco_id else banco_propio()
    _solo_propio(recurso)
    _registrar(recurso, actor)
    recalcular_dependientes(Partida.objects.filter(lineas__recurso=recurso).distinct(), actor)
    return recurso


# ── Copia de un banco externo al banco propio ──────────────────────────────────


def _origen(objeto):
    return f"{objeto.banco.nombre} · {objeto.codigo}"[:255]


def _codigo_libre(modelo, banco, codigo):
    codigo = codigo[: LARGO["codigo"]]
    if not modelo.objects.filter(banco=banco, codigo=codigo).exists():
        return codigo
    n = 2
    while True:
        sufijo = f"-{n}"
        candidato = codigo[: LARGO["codigo"] - len(sufijo)] + sufijo
        if not modelo.objects.filter(banco=banco, codigo=candidato).exists():
            return candidato
        n += 1


def _copiar_recurso(recurso, propio, actor, hechos):
    if recurso.pk in hechos:
        return hechos[recurso.pk]
    origen = _origen(recurso)
    copia = Recurso.objects.filter(banco=propio, origen=origen).first()
    if copia is None:
        precio, pendiente = recurso.precio, ""
        if recurso.tipo == Recurso.MANO_OBRA:
            coste_hora = coste_hora_actual()
            if coste_hora is None:
                pendiente = Recurso.PENDIENTE_COSTE_HORA
            else:
                precio = coste_hora
        elif recurso.tipo == Recurso.MATERIAL:
            pendiente = Recurso.PENDIENTE_TARIFA
        copia = Recurso(
            banco=propio,
            codigo=_codigo_libre(Recurso, propio, recurso.codigo),
            tipo=recurso.tipo,
            unidad=recurso.unidad,
            resumen=recurso.resumen,
            texto=recurso.texto,
            precio=precio,
            fecha_precio=recurso.fecha_precio,
            origen=origen,
            pendiente=pendiente,
        )
        _registrar(copia, actor, f"Copiado de {origen}")
    hechos[recurso.pk] = copia
    return copia


def _copiar_partida(partida, propio, actor, seccion, hechos, principal):
    clave = ("partida", partida.pk)
    if clave in hechos:
        return hechos[clave]
    origen = _origen(partida)
    if not principal:  # una auxiliar ya copiada antes se reutiliza
        existente = Partida.objects.filter(banco=propio, origen=origen).first()
        if existente is not None:
            hechos[clave] = existente
            return existente
    en_curso = hechos.setdefault("en_curso", set())
    if partida.pk in en_curso:
        raise ValidationError(f"El descompuesto de {partida.codigo} se incluye a sí mismo.")
    en_curso.add(partida.pk)
    # Primero los recursos y auxiliares: así la partida se da de alta ya con su precio y su
    # historial empieza con un «Alta» (copiada de …), sin un «Cambio» de recálculo detrás.
    lineas = []
    for linea in partida.lineas.select_related("recurso__banco", "auxiliar__banco"):
        if linea.recurso_id:
            recurso = _copiar_recurso(linea.recurso, propio, actor, hechos.setdefault("r", {}))
            auxiliar = None
        else:
            recurso = None
            auxiliar = _copiar_partida(linea.auxiliar, propio, actor, None, hechos, False)
        lineas.append(
            LineaDescompuesto(
                orden=linea.orden,
                recurso=recurso,
                auxiliar=auxiliar,
                factor=linea.factor,
                rendimiento=linea.rendimiento,  # los rendimientos se mantienen siempre
                merma_pct=linea.merma_pct,
            )
        )
    en_curso.discard(partida.pk)
    precio, fecha = partida.precio, partida.fecha_precio
    if lineas:
        precio, fecha = _precio_lineas(lineas), date.today()
    copia = Partida(
        banco=propio,
        seccion=seccion,
        codigo=_codigo_libre(Partida, propio, partida.codigo),
        unidad=partida.unidad,
        resumen=partida.resumen,
        texto=partida.texto,
        precio=precio,
        fecha_precio=fecha,
        auxiliar=not principal,
        origen=origen,
    )
    _registrar(copia, actor, f"Copiada de {origen}")
    hechos[clave] = copia
    for linea in lineas:
        linea.partida = copia
        _registrar(linea, actor)
    return copia


def _precio_lineas(lineas) -> Decimal | None:
    """Precio por unidad de unas líneas sin guardar (sus recursos y auxiliares ya tienen precio)."""
    return calculos.precio_partida(
        (ln.factor, ln.rendimiento, ln.merma_pct, ln.elemento.precio) for ln in lineas
    )


@transaction.atomic
def copiar_al_banco_propio(partida: Partida, actor, seccion=None) -> Partida:
    """Copia una partida de un banco externo (con su descompuesto, auxiliares y recursos) al
    banco propio, como partida independiente. Devuelve la copia, con su precio recalculado."""
    if partida.banco.es_propio:
        raise ValidationError("La partida ya es del banco propio.")
    propio = banco_propio()
    copia = _copiar_partida(partida, propio, actor, seccion, {}, True)
    return recalcular_precio(copia, actor)


# ── Descompuesto para las fichas ───────────────────────────────────────────────


@dataclass
class FilaDescompuesto:
    linea: LineaDescompuesto
    tipo: str
    cantidad: object
    precio: object
    importe: object


def filas_descompuesto(partida: Partida, ve_importes: bool):
    """Filas del descompuesto con cantidad por unidad, precio e importe (None si no se ven)."""
    filas = []
    for ln in partida.lineas.select_related("recurso", "auxiliar"):
        elemento = ln.elemento
        precio = elemento.precio
        tipo = ln.recurso.get_tipo_display() if ln.recurso_id else "Partida auxiliar"
        filas.append(
            FilaDescompuesto(
                linea=ln,
                tipo=tipo,
                cantidad=calculos.cantidad_por_unidad(ln.factor, ln.rendimiento, ln.merma_pct),
                precio=precio if ve_importes else None,
                importe=(
                    calculos.importe_linea(ln.factor, ln.rendimiento, ln.merma_pct, precio)
                    if ve_importes
                    else None
                ),
            )
        )
    return filas


# ── Importación de la plantilla de cálculo al banco propio ─────────────────────
#
# Cada hoja de partida se convierte en una partida del banco propio con su descompuesto:
# - materiales: un recurso por material (los nombres iguales sin tildes ni mayúsculas son el
#   mismo; los parecidos se proponen para unir y el usuario confirma), con el consumo como
#   rendimiento y la merma de la plantilla; quedan pendientes de enlazar con artículo y tarifa;
# - fijaciones y transportes (€ por unidad): recursos de 1 € con el importe como rendimiento;
# - mano de obra: el precio por unidad (F6) se convierte en rendimiento con el coste día
#   (rendimiento = coste_día / F6) y entra en horas por unidad (jornada / rendimiento).
#   El precio por unidad original queda en la partida como referencia.
# No se importan los gastos de empresa (14 %), el beneficio, los márgenes, el desplazamiento
# ni los resúmenes: son del presupuesto o son copias (lecciones de la plantilla).
# Una hoja que ya está en el banco (importada antes de esa hoja o una partida con el mismo
# nombre) nunca se sobrescribe sin preguntar: el usuario elige actualizarla, crear otra con
# código nuevo u omitirla. Sin cambios se omite; con cambios hay que elegir antes de importar.

CODIGO_MO, CODIGO_FIJACIONES, CODIGO_TRANSPORTES = "MO-OPERARIO", "FIJACIONES", "TRANSPORTES"
UMBRAL_PARECIDOS = 0.9  # «Ejecto pizarra» / «Efecto pizarra» ≈ 0,93
# Qué hacer con una hoja que ya existe en el banco ("" = sin elegir todavía)
ACTUALIZAR, CREAR, OMITIR = "actualizar", "crear", "omitir"
ACCIONES_EXISTENTE = [(ACTUALIZAR, "Actualizarla"), (CREAR, "Crear otra (código nuevo)"),
                      (OMITIR, "Omitir")]  # fmt: skip


def origen_plantilla(hoja: str) -> str:
    return f"{ORIGEN_PLANTILLA} · {hoja}"[:255]


def jornada_horas() -> Decimal:
    """Jornada de la empresa (8 h por defecto) para pasar del coste día al coste hora."""
    from empresa.models import Empresa

    empresa = Empresa.actual()
    return empresa.jornada_horas if empresa else Decimal("8")


@dataclass
class LineaPrevista:
    tipo: str  # material | fijaciones | transportes | mano_obra
    clave: str  # recurso: código si ya existe, o nombre normalizado si es nuevo
    resumen: str
    unidad: str
    rendimiento: Decimal
    merma_pct: Decimal
    precio: Decimal | None
    recurso: Recurso | None = None  # el del banco, si ya existe

    @property
    def importe(self):
        return calculos.importe_linea(Decimal("1"), self.rendimiento, self.merma_pct, self.precio)


@dataclass
class PartidaPrevista:
    indice: int
    hoja: object  # importadores.plantilla.HojaPartida
    accion: str  # crear | actualizar | omitir | "" (ya existe y falta elegir)
    codigo: str
    unidad: str
    existente: Partida | None  # la del banco con esa hoja o ese nombre
    otras: int = 0  # más partidas del banco con el mismo nombre
    lineas: list = field(default_factory=list)
    rendimiento_mo: Decimal | None = None
    horas_mo: Decimal | None = None
    errores: list = field(default_factory=list)
    cambia: bool = True

    @property
    def importar(self):
        return self.accion in (CREAR, ACTUALIZAR)

    @property
    def falta_elegir(self):
        return self.existente is not None and self.accion == ""

    @property
    def precio(self):
        return calculos.precio_partida(
            (Decimal("1"), ln.rendimiento, ln.merma_pct, ln.precio) for ln in self.lineas
        )

    @property
    def estado(self):
        if self.falta_elegir:
            return "falta elegir"
        if not self.importar:
            return "no se importa"
        if self.accion == CREAR:
            return "nueva"
        return "se actualiza" if self.cambia else "sin cambios"


@dataclass
class UnionPropuesta:
    """Dos materiales con nombres casi iguales. El usuario elige si son el mismo y con qué
    nombre se queda (`opciones`: [(valor, nombre)]; `elegida`: el valor elegido o "")."""

    clave: str
    nombres: tuple
    opciones: list
    elegida: str


@dataclass
class VistaPreviaPlantilla:
    resultado: object  # importadores.plantilla.ResultadoPlantilla
    fichero: str
    coste_dia: Decimal
    coste_dia_provisional: bool
    jornada: Decimal
    partidas: list
    uniones: list
    avisos: list

    @property
    def coste_hora(self):
        return calculos.coste_hora_de_dia(self.coste_dia, self.jornada)

    @property
    def lecciones(self):
        from importadores.plantilla import Leccion

        lecciones = list(self.resultado.lecciones)
        if self.uniones:
            lecciones.append(
                Leccion(5, f"{len(self.uniones)} pareja(s) de materiales con nombres casi iguales "
                        "(posibles erratas): elige cuáles son el mismo material.")
            )  # fmt: skip
        return sorted(lecciones, key=lambda leccion: leccion.numero)

    @property
    def errores(self):
        return list(self.resultado.errores) + [
            f"{p.hoja.nombre}: {e}" for p in self.partidas if p.importar for e in p.errores
        ] + [
            f"{p.hoja.nombre}: ya existe en el banco ({p.existente.codigo}) y ha cambiado; elige "
            "si se actualiza, se crea otra con código nuevo o se omite."
            for p in self.partidas if p.falta_elegir
        ]  # fmt: skip

    @property
    def a_importar(self):
        return [p for p in self.partidas if p.importar]

    @property
    def se_puede_importar(self):
        return bool(self.a_importar) and not self.errores

    @property
    def recuento(self):
        estados = [p.estado for p in self.partidas]
        return {
            "nuevas": estados.count("nueva"),
            "actualizadas": estados.count("se actualiza"),
            "sin_cambios": estados.count("sin cambios"),
            "omitidas": estados.count("no se importa"),
            "pendientes": estados.count("falta elegir"),
        }


def _proponer_uniones(nombres_fichero, existentes):
    """Parejas (a, b) de nombres normalizados casi iguales; al menos uno es del fichero."""
    import difflib

    candidatos = list(nombres_fichero) + [n for n in existentes if n not in nombres_fichero]
    parejas = []
    for i, a in enumerate(nombres_fichero):
        for b in candidatos[i + 1 :]:
            if a == b or (a in existentes and b in existentes):
                continue
            comparador = difflib.SequenceMatcher(None, a, b)
            umbral = UMBRAL_PARECIDOS
            if comparador.quick_ratio() >= umbral and comparador.ratio() >= umbral:
                parejas.append((a, b))
    return parejas


def _destino(clave, uniones):
    vistos = set()
    while clave in uniones and clave not in vistos:
        vistos.add(clave)
        clave = uniones[clave]
    return clave


def _siguientes_codigos(modelo, banco, prefijo, usados):
    """Generador de códigos libres «P-001», «P-002»… en el banco (y fuera de `usados`)."""
    import re

    patron = re.compile(rf"^{re.escape(prefijo)}(\d+)$")
    tomados = set(modelo.objects.filter(banco=banco, codigo__startswith=prefijo)
                  .values_list("codigo", flat=True)) | set(usados)  # fmt: skip
    n = max((int(m.group(1)) for c in tomados if (m := patron.match(c))), default=0)
    while True:
        n += 1
        codigo = f"{prefijo}{n:03d}"
        if codigo not in tomados:
            yield codigo


def _coste_dia_para(resultado, pedido):
    """(coste día, provisional). Con coste hora de la empresa, coste hora × jornada; sin él, el
    que indique el usuario o, si no, el de la plantilla (provisional)."""
    from importadores.plantilla import COSTE_DIA_PLANTILLA

    jornada = jornada_horas()
    coste_hora = coste_hora_actual()
    if coste_hora is not None:
        return calculos.coste_dia(coste_hora, jornada), False
    if pedido is not None and pedido > 0:
        return pedido, True
    return resultado.coste_dia or COSTE_DIA_PLANTILLA, True


def _lineas_iguales(partida, lineas):
    actuales = [
        (ln.recurso.codigo if ln.recurso_id else None, ln.factor, ln.rendimiento, ln.merma_pct)
        for ln in partida.lineas.select_related("recurso")
    ]
    nuevas = [
        (ln.recurso.codigo if ln.recurso else None, Decimal("1"), ln.rendimiento, ln.merma_pct)
        for ln in lineas
    ]
    return actuales == nuevas


def _partida_cambia(prevista, mano_obra, coste_hora):
    """¿Actualizar la partida del banco con la hoja cambiaría algo?"""
    e, p = prevista.existente, prevista
    mo_ref = calculos.redondear_d4(p.hoja.precio_mo)[0]
    return bool(
        (e.codigo, e.unidad, e.resumen, e.precio_mo_referencia)
        != (p.codigo, p.unidad, p.hoja.nombre[: LARGO["resumen"]], mo_ref)
        or any(ln.recurso is None for ln in p.lineas)
        or not _lineas_iguales(e, p.lineas)
        or (mano_obra and p.horas_mo and mano_obra.precio != coste_hora)
    )


def preparar_plantilla(datos: bytes, fichero: str, *, coste_dia=None, ajustes=None,
                       uniones=()) -> VistaPreviaPlantilla:  # fmt: skip
    """Lee la plantilla y dice qué se importaría al banco propio, sin guardar nada.

    `coste_dia`: el que fija el usuario mientras no haya coste hora de la empresa.
    `ajustes`: {índice de partida: {"importar": bool, "accion": str, "codigo": str,
    "unidad": str}}. `importar` vale para las hojas nuevas; `accion` (actualizar, crear u
    omitir), para las que ya existen en el banco: sin ella, se omiten si no cambian y, si
    cambian, queda sin elegir y no se puede importar.
    `uniones`: parejas de materiales que el usuario confirma como el mismo, «clave>destino»
    (los valores de `UnionPropuesta.opciones`).
    """
    from importadores.plantilla import leer_plantilla, normalizar

    resultado = leer_plantilla(datos)
    propio = banco_propio()
    ajustes = ajustes or {}
    jornada = jornada_horas()
    coste_dia, provisional = _coste_dia_para(resultado, coste_dia)
    coste_hora = calculos.coste_hora_de_dia(coste_dia, jornada)
    avisos = list(resultado.avisos)

    # Materiales: nombres del fichero, los del banco y las uniones
    materiales_banco = {}
    for recurso in Recurso.objects.filter(banco=propio, tipo=Recurso.MATERIAL).order_by("pk"):
        materiales_banco.setdefault(normalizar(recurso.resumen), recurso)
    nombres = {}
    for hoja in resultado.partidas:
        for m in hoja.materiales:
            nombres.setdefault(normalizar(m.nombre), m)
    # Uniones elegidas: «a|b>destino» (el que queda). Con uno ya en el banco, queda ese.
    elegidas = dict(u.partition(">")[::2] for u in uniones)
    lista_uniones, mapa = [], {}
    for a, b in _proponer_uniones(list(nombres), materiales_banco):
        clave = f"{a}|{b}"

        def visible(n):
            return materiales_banco[n].resumen if n in materiales_banco else nombres[n].nombre

        destinos = [n for n in (a, b) if n in materiales_banco] or [a, b]
        opciones = [(f"{clave}>{d}", visible(d)) for d in destinos]
        destino = elegidas.get(clave)
        if destino not in destinos:
            destino = None
        lista_uniones.append(UnionPropuesta(
            clave, (visible(a), visible(b)), opciones, f"{clave}>{destino}" if destino else ""
        ))  # fmt: skip
        if destino:
            mapa[b if destino == a else a] = destino

    precios_vistos = {}
    for clave, m in nombres.items():
        final = _destino(clave, mapa)
        existente = materiales_banco.get(final)
        neto = calculos.redondear_d4(m.precio_neto)[0]
        if existente is not None and neto is not None and existente.precio != neto:
            avisos.append(
                f"«{m.nombre}» ya está en el banco ({existente.codigo}) con otro precio: se "
                "mantiene el del banco."
            )
        anterior = precios_vistos.setdefault(final, neto)
        if anterior != neto and existente is None:
            avisos.append(f"«{m.nombre}» tiene precios distintos en varias hojas: vale el primero.")

    recursos_fijos = {
        r.codigo: r
        for r in Recurso.objects.filter(
            banco=propio, origen=ORIGEN_PLANTILLA,
            codigo__in=[CODIGO_MO, CODIGO_FIJACIONES, CODIGO_TRANSPORTES],
        )
    }  # fmt: skip

    # Partidas
    # Ya en el banco: primero la importada de esa hoja; después, las del mismo nombre
    por_origen, por_nombre = {}, {}
    for partida in Partida.objects.filter(banco=propio).order_by("pk"):
        if partida.origen.startswith(ORIGEN_PLANTILLA):
            por_origen.setdefault(partida.origen, []).append(partida)
        por_nombre.setdefault(normalizar(partida.resumen), []).append(partida)
    codigos_nuevos = _siguientes_codigos(Partida, propio, "P-", ())
    validas = {ACTUALIZAR, CREAR, OMITIR, ""}
    partidas = []
    for indice, hoja in enumerate(resultado.partidas):
        ajuste = ajustes.get(indice, {})
        candidatas = list(por_origen.get(origen_plantilla(hoja.nombre), []))
        mismo_nombre = por_nombre.get(normalizar(hoja.nombre), [])
        candidatas += [c for c in mismo_nombre if c not in candidatas]
        existente = candidatas[0] if candidatas else None
        if existente is None:
            omitir = ajuste.get("accion") == OMITIR or not ajuste.get("importar", True)
            accion = OMITIR if omitir else CREAR
        else:  # None: se decide abajo según haya cambios
            accion = ajuste["accion"] if ajuste.get("accion") in validas else None
        codigo = (ajuste.get("codigo") or "").strip()
        if existente and accion == CREAR and codigo in ("", existente.codigo):
            codigo = next(codigos_nuevos)
        if not codigo:
            codigo = existente.codigo if existente else next(codigos_nuevos)
        unidad = (ajuste.get("unidad") or "").strip()
        if not unidad:
            de_existente = existente and existente.unidad and accion != CREAR
            unidad = existente.unidad if de_existente else hoja.unidad
        prevista = PartidaPrevista(
            indice, hoja, accion or "", codigo, unidad, existente, len(candidatas[1:])
        )
        for m in hoja.materiales:
            final = _destino(normalizar(m.nombre), mapa)
            recurso = materiales_banco.get(final)
            base = nombres.get(final)
            prevista.lineas.append(
                LineaPrevista(
                    "material", recurso.codigo if recurso else final,
                    recurso.resumen if recurso else (base.nombre if base else m.nombre),
                    recurso.unidad if recurso else "",
                    calculos.redondear_d4(m.consumo)[0], m.merma_pct,
                    recurso.precio if recurso else precios_vistos.get(final), recurso,
                )
            )  # fmt: skip
        for tipo, codigo_fijo, resumen, importe in (
            ("fijaciones", CODIGO_FIJACIONES, "Fijaciones", hoja.fijaciones),
            ("transportes", CODIGO_TRANSPORTES, "Transportes de material", hoja.transportes),
        ):
            if importe:
                prevista.lineas.append(
                    LineaPrevista(tipo, codigo_fijo, resumen, "€",
                                  calculos.redondear_d4(importe)[0], Decimal("0"), Decimal("1"),
                                  recursos_fijos.get(codigo_fijo))
                )  # fmt: skip
        if hoja.precio_mo:
            prevista.rendimiento_mo = calculos.rendimiento_mo(coste_dia, hoja.precio_mo)
            prevista.horas_mo = calculos.horas_por_unidad(jornada, coste_dia, hoja.precio_mo)
            prevista.lineas.append(
                LineaPrevista("mano_obra", CODIGO_MO, "Mano de obra (operario)", "h",
                              prevista.horas_mo, Decimal("0"), coste_hora,
                              recursos_fijos.get(CODIGO_MO))
            )  # fmt: skip
        if existente is not None:
            prevista.cambia = _partida_cambia(prevista, recursos_fijos.get(CODIGO_MO), coste_hora)
            if accion is None:
                prevista.accion = "" if prevista.cambia else OMITIR
        partidas.append(prevista)

    # Comprobaciones de lo que se va a importar
    otros = dict(Partida.objects.filter(banco=propio).values_list("codigo", "pk"))
    usados = {}
    for p in partidas:
        if not p.importar:
            continue
        if len(p.codigo) > LARGO["codigo"]:
            p.errores.append(f"el código pasa de {LARGO['codigo']} caracteres.")
        if p.codigo in usados:
            p.errores.append(f"el código {p.codigo} se repite en «{usados[p.codigo]}».")
        usados.setdefault(p.codigo, p.hoja.nombre)
        dueno = otros.get(p.codigo)
        if dueno is not None and not (p.accion == ACTUALIZAR and dueno == p.existente.pk):
            p.errores.append(f"el código {p.codigo} ya es de otra partida del banco propio.")
        if not p.unidad or len(p.unidad) > LARGO["unidad"]:
            p.errores.append("falta la unidad (o pasa de 20 caracteres).")

    return VistaPreviaPlantilla(
        resultado, fichero, coste_dia, provisional, jornada, partidas, lista_uniones, avisos
    )


@dataclass
class ResumenPlantilla:
    creadas: list = field(default_factory=list)
    actualizadas: list = field(default_factory=list)
    sin_cambios: list = field(default_factory=list)
    recursos_creados: int = 0


def _recurso_plantilla(propio, codigo, actor, motivo, **valores):
    recurso = Recurso.objects.filter(banco=propio, origen=ORIGEN_PLANTILLA, codigo=codigo).first()
    if recurso is None:
        recurso = Recurso(banco=propio, codigo=_codigo_libre(Recurso, propio, codigo),
                          origen=ORIGEN_PLANTILLA, **valores)  # fmt: skip
        _registrar(recurso, actor, motivo)
        return recurso, True
    if recurso.precio != valores["precio"] or recurso.pendiente != valores.get("pendiente", ""):
        recurso.precio = valores["precio"]
        recurso.pendiente = valores.get("pendiente", "")
        recurso.fecha_precio = date.today()
        guardar_recurso(recurso, actor)
    return recurso, False


@transaction.atomic
def importar_plantilla(datos: bytes, fichero: str, actor, *, coste_dia=None, ajustes=None,
                       uniones=(), seccion=None) -> ResumenPlantilla:  # fmt: skip
    """Importa las hojas de partida de la plantilla al banco propio (ver `preparar_plantilla`).
    Las partidas nuevas reciben `seccion`; las que ya estaban conservan la suya."""
    vista = preparar_plantilla(datos, fichero, coste_dia=coste_dia, ajustes=ajustes,
                               uniones=uniones)  # fmt: skip
    if not vista.se_puede_importar:
        raise ValidationError(vista.errores or ["No hay partidas que importar: todas se omiten."])
    propio = banco_propio()
    motivo = f"Importado de la plantilla {fichero}"[:100]
    resumen = ResumenPlantilla()
    hoy = date.today()

    fijos = {}
    necesarios = {ln.tipo for p in vista.a_importar for ln in p.lineas}
    if "mano_obra" in necesarios:
        fijos["mano_obra"], nuevo = _recurso_plantilla(
            propio, CODIGO_MO, actor, motivo, tipo=Recurso.MANO_OBRA, unidad="h",
            resumen="Mano de obra (operario)", precio=vista.coste_hora, fecha_precio=hoy,
            pendiente=Recurso.PENDIENTE_COSTE_HORA if vista.coste_dia_provisional else "",
        )  # fmt: skip
        resumen.recursos_creados += nuevo
    for tipo, codigo, texto in (
        ("fijaciones", CODIGO_FIJACIONES, "Fijaciones"),
        ("transportes", CODIGO_TRANSPORTES, "Transportes de material"),
    ):
        if tipo in necesarios:
            fijos[tipo], nuevo = _recurso_plantilla(
                propio, codigo, actor, motivo, tipo=Recurso.OTRO, unidad="€", resumen=texto,
                precio=Decimal("1"), fecha_precio=hoy,
            )  # fmt: skip
            resumen.recursos_creados += nuevo

    materiales = {}
    codigos_material = _siguientes_codigos(Recurso, propio, "M-", ())
    for p in vista.a_importar:
        for ln in p.lineas:
            if ln.tipo == "material":
                if ln.recurso is None and ln.clave not in materiales:
                    nuevo = Recurso(
                        banco=propio, codigo=next(codigos_material), tipo=Recurso.MATERIAL,
                        resumen=ln.resumen[: LARGO["resumen"]], precio=ln.precio,
                        fecha_precio=hoy, origen=ORIGEN_PLANTILLA,
                        pendiente=Recurso.PENDIENTE_TARIFA,
                    )  # fmt: skip
                    _registrar(nuevo, actor, motivo)
                    materiales[ln.clave] = nuevo
                    resumen.recursos_creados += 1
                ln.recurso = ln.recurso or materiales[ln.clave]
            else:
                ln.recurso = fijos[ln.tipo]

    for p in vista.a_importar:
        lineas = [
            LineaDescompuesto(orden=n, recurso=ln.recurso, factor=Decimal("1"),
                              rendimiento=ln.rendimiento, merma_pct=ln.merma_pct)
            for n, ln in enumerate(p.lineas)
        ]  # fmt: skip
        mo_ref = calculos.redondear_d4(p.hoja.precio_mo)[0]
        partida = p.existente if p.accion == ACTUALIZAR else None
        if partida is None:
            partida = Partida(
                banco=propio, seccion=seccion, codigo=p.codigo, unidad=p.unidad,
                resumen=p.hoja.nombre[: LARGO["resumen"]], precio=_precio_lineas(lineas),
                fecha_precio=hoy, origen=origen_plantilla(p.hoja.nombre),
                precio_mo_referencia=mo_ref,
            )  # fmt: skip
            _registrar(partida, actor, motivo)
            for linea in lineas:
                linea.partida = partida
                _registrar(linea, actor)
            resumen.creadas.append(partida)
            continue
        if not p.cambia:
            resumen.sin_cambios.append(partida)
            continue
        campos = ("codigo", "unidad", "resumen", "precio_mo_referencia", "seccion_id", "precio")
        antes = [getattr(partida, c) for c in campos]
        partida.codigo, partida.unidad = p.codigo, p.unidad
        partida.resumen, partida.precio_mo_referencia = p.hoja.nombre[: LARGO["resumen"]], mo_ref
        if seccion is not None and partida.seccion_id is None:
            partida.seccion = seccion
        if not _lineas_iguales(partida, lineas):
            for vieja in partida.lineas.all():
                vieja.delete()
            for linea in lineas:
                linea.partida = partida
                _registrar(linea, actor)
        nuevo_precio = _precio_lineas(lineas)
        if nuevo_precio != partida.precio:
            partida.precio, partida.fecha_precio = nuevo_precio, hoy
        if antes != [getattr(partida, c) for c in campos]:
            _registrar(partida, actor, motivo)
        recalcular_dependientes([partida], actor)
        resumen.actualizadas.append(partida)
    return resumen
