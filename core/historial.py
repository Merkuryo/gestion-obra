"""Historial de una ficha a partir de django-simple-history: quién, cuándo y qué cambió.

Lo usa el componente `{% historial objeto %}` (core/templatetags/componentes.py). Para que una
ficha tenga historial, su modelo necesita `history = HistoricalRecords()` y `parte_permisos`
(la parte de core/permisos.PARTES que hay que poder ver). Opcionalmente:

- `campos_importe`: importes y costes (coste hora y similares), que solo ve quien tiene
  «ver importes» en esa parte;
- `campos_margen`: márgenes y beneficio, que solo ve quien tiene «ver márgenes» en esa parte;
- `campos_nominas`: campos que solo ve quien tiene el permiso de nóminas;
- `baja_si`: {campo: valor} que convierte un cambio en baja o anulación
  (p. ej. {"activa": False} al archivar, {"estado": "anulado"} en un documento);
- `historial_relacionado(self)`: lista de `Relacionado` con el historial de otros modelos que
  se muestran en la misma ficha (p. ej. los permisos de un rol).

A quien no puede ver un campo se le muestra «(oculto)» en lugar de sus valores.
"""

import heapq
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.db import models
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.text import capfirst

from . import permisos

POR_PAGINA = 20
_BLOQUE = 50  # registros históricos que se leen de cada vez
OCULTO = "(oculto)"
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
    de un documento…). `titulo(registro)` encabeza cada entrada; `excluir` son campos que no se
    listan porque ya los dice el título."""

    registros: models.QuerySet
    titulo: Callable
    excluir: tuple = ()


@dataclass
class Cambio:
    campo: str
    antes: str
    despues: str


@dataclass
class Entrada:
    fecha: datetime
    usuario: str
    accion: str
    titulo: str = ""
    cambios: list = field(default_factory=list)
    motivo: str = ""

    @property
    def texto_accion(self):
        return ACCIONES[self.accion][0]

    @property
    def color(self):
        return ACCIONES[self.accion][1]


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
    return [
        f for f in modelo._meta.concrete_fields if f.name not in EXCLUIDOS and f.name not in excluir
    ]


def _texto(campo, valor):
    """Valor guardado → texto legible: choices y FK por su nombre, Sí/No, cifras en español."""
    if valor is None or valor == "" or valor == [] or valor == {}:
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
        return formats.number_format(valor, use_l10n=True, force_grouping=True)
    return str(valor)


def _cambios(registro, anterior, campos, ocultos):
    cambios = []
    for campo in campos:
        nuevo = getattr(registro, campo.attname)
        if anterior is None:
            viejo = campo.get_default()
            if nuevo == viejo or (nuevo in ("", None) and viejo in ("", None)):
                continue
        else:
            viejo = getattr(anterior, campo.attname)
            if nuevo == viejo:
                continue
        nombre = capfirst(campo.verbose_name)
        if campo.name in ocultos:
            cambios.append(Cambio(nombre, OCULTO, OCULTO))
        else:
            antes = VACIO if anterior is None else _texto(campo, viejo)
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


def _entrada(registro, fuente, ocultos_de):
    """Entrada legible de un registro histórico, o None si no hay nada que contar
    (un guardado sin cambios ni motivo)."""
    modelo = registro.instance_type
    accion = _TIPOS[registro.history_type]
    anterior = registro.prev_record if accion != ALTA else None
    if accion == BAJA:
        cambios = []
    else:
        cambios = _cambios(registro, anterior, _campos(modelo, fuente.excluir), ocultos_de(modelo))
        if accion == CAMBIO and _es_baja(registro, anterior, modelo):
            accion = BAJA
    motivo = registro.history_change_reason or ""
    if accion == CAMBIO and not cambios and not motivo:
        return None
    return Entrada(
        fecha=registro.history_date,
        usuario=_usuario(registro),
        accion=accion,
        titulo=fuente.titulo(registro) if fuente.titulo else "",
        cambios=cambios,
        motivo=motivo,
    )


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
    entradas, hay_mas = [], False
    for registro, fuente in mezclados:
        entrada = _entrada(registro, fuente, ocultos_de)
        if entrada is None:
            continue
        if len(entradas) == por_pagina:
            hay_mas = True
            break
        entradas.append(entrada)

    siguiente = url_mas(objeto, entradas[-1].fecha) if hay_mas else None
    return Pagina(entradas=entradas, primera=antes is None, url_mas=siguiente)
