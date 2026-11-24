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

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from importadores.bc3 import ResultadoBC3, leer_bc3

from . import calculos
from .models import Banco, Capitulo, LineaDescompuesto, Partida, Recurso

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
    copia = Partida(
        banco=propio,
        seccion=seccion,
        codigo=_codigo_libre(Partida, propio, partida.codigo),
        unidad=partida.unidad,
        resumen=partida.resumen,
        texto=partida.texto,
        precio=partida.precio,
        fecha_precio=partida.fecha_precio,
        auxiliar=not principal,
        origen=origen,
    )
    _registrar(copia, actor, f"Copiada de {origen}")
    hechos[clave] = copia
    for linea in partida.lineas.select_related("recurso__banco", "auxiliar__banco"):
        if linea.recurso_id:
            recurso = _copiar_recurso(linea.recurso, propio, actor, hechos.setdefault("r", {}))
            auxiliar = None
        else:
            recurso = None
            auxiliar = _copiar_partida(linea.auxiliar, propio, actor, None, hechos, False)
        nueva = LineaDescompuesto(
            partida=copia,
            orden=linea.orden,
            recurso=recurso,
            auxiliar=auxiliar,
            factor=linea.factor,
            rendimiento=linea.rendimiento,  # los rendimientos se mantienen siempre
            merma_pct=linea.merma_pct,
        )
        _registrar(nueva, actor)
    return copia


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
