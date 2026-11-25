"""Historial de una ficha a partir de django-simple-history: quién, cuándo y qué cambió.

Lo usa el componente `{% historial objeto %}` (core/templatetags/componentes.py). Para que una
ficha tenga historial, su modelo necesita `history = HistoricalRecords()` y `parte_permisos`
(la parte de core/permisos.PARTES que hay que poder ver). Opcionalmente:

- `campos_importe`: importes y costes (coste hora y similares), que solo ve quien tiene
  «ver importes» en esa parte y que se muestran con 2 decimales;
- `campos_margen`: márgenes y beneficio, que solo ve quien tiene «ver márgenes» en esa parte;
- `campos_nominas`: campos que solo ve quien tiene el permiso de nóminas;
- `campos_internos`: campos técnicos que no se muestran (p. ej. el grupo de Django de un rol);
- `baja_si`: {campo: valor} que convierte un cambio en baja o anulación
  (p. ej. {"activa": False} al archivar, {"estado": "anulado"} en un documento);
- `historial_relacionado(self)`: lista de `Relacionado` con el historial de otros modelos que
  se muestran en la misma ficha (p. ej. los permisos de un rol).

Cada entrada es un guardado: los registros del mismo usuario hechos a la vez (en pocos segundos
y sin repetir objeto) se juntan, p. ej. un rol con sus permisos y sus usuarios. En un alta
salen todos los campos con valor; en un cambio, solo los que cambian.

A quien no puede ver un campo se le muestra «(oculto)» en lugar de sus valores (la misma
regla que en listas y formularios: permisos.campos_ocultos).
"""

import heapq
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

from django.db import models
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.text import capfirst

from . import permisos

POR_PAGINA = 20
_BLOQUE = 50  # registros históricos que se leen de cada vez
# Registros del mismo usuario con menos de esta diferencia forman un mismo guardado
VENTANA_GUARDADO = timedelta(seconds=5)
OCULTO = permisos.OCULTO
VACIO = "—"
# Campos de auditoría: ya se ven en las columnas de fecha y usuario
EXCLUIDOS = {"id", "creado_en", "modificado_en", "creado_por", "modificado_por"}

ALTA, CAMBIO, BAJA = "alta", "cambio", "baja"
ACCIONES = {
    ALTA: ("Alta", "success"),
    CAMBIO: ("Cambio", "primary"),
    BAJA: ("Baja / anulación", "danger"),
}
_TIPOS = {"+": ALTA, "~": CAMBIO, "-": BAJA}


@dataclass
class Relacionado:
    """Historial de otro modelo que se enseña en la ficha (los permisos de un rol, las líneas
    de un documento…). `titulo(registro)` encabeza su parte de la entrada; `excluir` son campos
    que no se listan porque ya los dice el título."""

    registros: models.QuerySet
    titulo: Callable
    excluir: tuple = ()


@dataclass
class Cambio:
    campo: str
    antes: str
    despues: str


@dataclass
class Bloque:
    """Lo que un guardado cambió en un objeto: la ficha (sin título) o un relacionado."""

    titulo: str = ""
    cambios: list = field(default_factory=list)
    nota: str = ""  # p. ej. «Eliminado» para un relacionado que se borra


@dataclass
class Entrada:
    fecha: datetime
    usuario: str
    accion: str
    bloques: list = field(default_factory=list)
    motivo: str = ""

    @property
    def texto_accion(self):
        return ACCIONES[self.accion][0]

    @property
    def color(self):
        return ACCIONES[self.accion][1]

    @property
    def cambios(self):
        """Todos los cambios de la entrada, de todos sus bloques."""
        return [c for b in self.bloques for c in b.cambios]

    def bloque(self, titulo=""):
        return next((b for b in self.bloques if b.titulo == titulo), None)


@dataclass
class Pagina:
    entradas: list
    primera: bool
    url_mas: str | None = None


@dataclass
class _Fuente:
    registros: models.QuerySet
    titulo: Callable | None = None
    excluir: tuple = ()


def _campos(modelo, excluir):
    fuera = EXCLUIDOS | set(excluir) | set(getattr(modelo, "campos_internos", ()))
    return [f for f in modelo._meta.concrete_fields if f.name not in fuera]


def _vacio(valor):
    return valor is None or valor == "" or valor == [] or valor == {}


def _texto(campo, valor):
    """Valor guardado → texto legible: choices y FK por su nombre, Sí/No, cifras en español."""
    if _vacio(valor):
        return VACIO
    if campo.choices:
        return str(dict(campo.flatchoices).get(valor, valor))
    if campo.is_relation:
        relacionado = campo.related_model._default_manager.filter(pk=valor).first()
        return str(relacionado) if relacionado else f"#{valor} (ya no existe)"
    if isinstance(campo, models.BooleanField):
        return "Sí" if valor else "No"
    if isinstance(campo, models.JSONField):
        return json.dumps(valor, ensure_ascii=False)
    if isinstance(valor, datetime):
        return formats.date_format(timezone.localtime(valor), "d/m/Y H:i")
    if isinstance(valor, date):
        return formats.date_format(valor, "d/m/Y")
    if isinstance(valor, (Decimal, int, float)):
        # Importes y precios en pantalla con 2 decimales, redondeados (se guardan con los que
        # tengan; number_format solo cortaría)
        if campo.name in getattr(campo.model, "campos_importe", ()) and isinstance(valor, Decimal):
            valor = valor.quantize(Decimal("0.01"), ROUND_HALF_UP)
        return formats.number_format(valor, use_l10n=True, force_grouping=True)
    return str(valor)


def _cambios(registro, anterior, campos, ocultos):
    """En un alta (sin `anterior`), todos los campos con valor; en un cambio, los que cambian."""
    cambios = []
    for campo in campos:
        nuevo = getattr(registro, campo.attname)
        if anterior is None:
            if _vacio(nuevo):
                continue
        elif nuevo == getattr(anterior, campo.attname):
            continue
        nombre = capfirst(campo.verbose_name)
        if campo.name in ocultos:
            cambios.append(Cambio(nombre, OCULTO, OCULTO))
        else:
            antes = VACIO if anterior is None else _texto(campo, getattr(anterior, campo.attname))
            cambios.append(Cambio(nombre, antes, _texto(campo, nuevo)))
    return cambios


def _es_baja(registro, anterior, modelo):
    for campo, valor in getattr(modelo, "baja_si", {}).items():
        antes = None if anterior is None else getattr(anterior, campo)
        if getattr(registro, campo) == valor and antes != valor:
            return True
    return False


def _usuario(registro):
    usuario = registro.history_user
    if usuario is None:
        return "Sistema"
    return usuario.get_full_name() or usuario.get_username()


def _parte(registro, fuente, ocultos_de):
    """(acción, bloque, motivo) de un registro histórico."""
    modelo = registro.instance_type
    accion = _TIPOS[registro.history_type]
    anterior = registro.prev_record if accion == CAMBIO else None
    titulo = fuente.titulo(registro) if fuente.titulo else ""
    if accion == BAJA:
        bloque = Bloque(titulo, nota="Eliminado" if titulo else "")
    else:
        campos = _campos(modelo, fuente.excluir)
        bloque = Bloque(titulo, _cambios(registro, anterior, campos, ocultos_de(modelo)))
        if accion == CAMBIO and _es_baja(registro, anterior, modelo):
            accion = BAJA
    return accion, bloque, registro.history_change_reason or ""


def _entrada(grupo, ocultos_de):
    """Una entrada por guardado, o None si no hay nada que contar (guardado sin cambios)."""
    partes = [(fuente, *_parte(r, fuente, ocultos_de)) for r, fuente in reversed(grupo)]
    principal = next((p for p in partes if p[0].titulo is None), None)
    accion = principal[1] if principal else partes[0][1]
    if principal is None and accion == BAJA:
        accion = CAMBIO  # borrar un relacionado es un cambio de la ficha
    elif principal is None and accion == ALTA:
        accion = CAMBIO  # añadir un relacionado a una ficha que ya existía
    # La ficha primero, luego los relacionados en el orden en que se guardaron
    partes.sort(key=lambda p: p[0].titulo is not None)
    bloques = [b for _, _, b, _ in partes if b.cambios or b.nota]
    motivo = "; ".join(m for *_, m in partes if m)
    if accion == CAMBIO and not bloques and not motivo:
        return None
    ultimo = grupo[0][0]
    return Entrada(
        fecha=ultimo.history_date,
        usuario=_usuario(ultimo),
        accion=accion,
        bloques=bloques,
        motivo=motivo,
    )


def _clave(registro):
    return registro.instance_type, getattr(registro, registro.instance_type._meta.pk.attname)


def _mismo_guardado(grupo, registro):
    """¿`registro` es del mismo guardado que `grupo`? Mismo usuario, pocos segundos y un objeto
    que aún no está en el grupo (dos guardados seguidos de la misma ficha no se juntan)."""
    primero = grupo[0][0]
    return (
        registro.history_user_id == primero.history_user_id
        and primero.history_date - registro.history_date <= VENTANA_GUARDADO
        and _clave(registro) not in {_clave(r) for r, _ in grupo}
    )


def _guardados(mezclados):
    grupo = []
    for registro, fuente in mezclados:
        if grupo and _mismo_guardado(grupo, registro):
            grupo.append((registro, fuente))
            continue
        if grupo:
            yield grupo
        grupo = [(registro, fuente)]
    if grupo:
        yield grupo


def _flujo(fuente, antes):
    registros = fuente.registros.select_related("history_user")
    if antes is not None:
        registros = registros.filter(history_date__lt=antes)
    registros = registros.order_by("-history_date", "-history_id")
    desde = 0
    while True:  # por bloques, sin cursores de servidor abiertos entre consultas
        bloque = list(registros[desde : desde + _BLOQUE])
        for registro in bloque:
            yield registro, fuente
        if len(bloque) < _BLOQUE:
            return
        desde += _BLOQUE


def url_mas(objeto, antes):
    url = reverse(
        "historial_mas", args=[objeto._meta.app_label, objeto._meta.model_name, objeto.pk]
    )
    return f"{url}?{urlencode({'antes': antes.isoformat()})}"


def historial(objeto, usuario, antes=None, por_pagina=POR_PAGINA) -> Pagina:
    """Las `por_pagina` entradas más recientes del historial de `objeto` (y sus relacionados),
    anteriores a `antes` si se indica. No comprueba si el usuario puede ver la ficha: eso lo
    hace quien llama, con permisos.puede_ver_historial."""
    fuentes = [_Fuente(objeto.history.all())]
    for rel in getattr(objeto, "historial_relacionado", lambda: [])():
        fuentes.append(_Fuente(rel.registros, rel.titulo, rel.excluir))

    cache_ocultos = {}

    def ocultos_de(modelo):
        if modelo not in cache_ocultos:
            cache_ocultos[modelo] = permisos.campos_ocultos(usuario, objeto, modelo)
        return cache_ocultos[modelo]

    flujos = [_flujo(f, antes) for f in fuentes]
    mezclados = heapq.merge(*flujos, key=lambda par: par[0].history_date, reverse=True)
    entradas, hay_mas, desde = [], False, None
    for grupo in _guardados(mezclados):
        entrada = _entrada(grupo, ocultos_de)
        if entrada is None:
            continue
        if len(entradas) == por_pagina:
            hay_mas = True
            break
        entradas.append(entrada)
        desde = grupo[-1][0].history_date  # el registro más antiguo del guardado

    siguiente = url_mas(objeto, desde) if hay_mas else None
    return Pagina(entradas=entradas, primera=antes is None, url_mas=siguiente)
