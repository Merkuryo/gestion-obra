"""Buscador de partidas (pantalla propia) y selector de partida reutilizable.

La búsqueda está en catalogos/busqueda.py; aquí solo se preparan las filas para pintarlas.
"""

import json

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View

from core import unidades
from core.permisos import ConPermiso, campos_ocultos, puede_editar
from core.secciones import secciones_del_selector

from . import busqueda
from .models import Banco, Partida

MODULO = "banco"
POR_PAGINA_SELECTOR = 10


def _url_ficha(partida):
    if partida.banco.es_propio:
        return reverse("catalogos:partida", args=[partida.pk])
    return reverse("catalogos:partida_externa", args=[partida.banco_id, partida.pk])


def _filas(resultado, usuario, unidad_destino=""):
    filas = []
    for partida in resultado.partidas:
        oculta = "precio" in campos_ocultos(usuario, partida)
        filas.append({
            "partida": partida,
            "resumen": busqueda.resaltar(partida.resumen, resultado.terminos),
            "codigo": busqueda.resaltar(partida.codigo, resultado.terminos),
            "fragmento": busqueda.fragmento(partida.texto, resultado.terminos),
            "url": _url_ficha(partida),
            "precio_oculto": oculta,
            "otra_unidad": bool(unidad_destino)
            and not unidades.misma_unidad(partida.unidad, unidad_destino),
        })  # fmt: skip
    return filas


def _sin_pagina(request):
    datos = request.GET.copy()
    datos.pop("pagina", None)
    return datos


class BuscarPartidas(ConPermiso, View):
    """Pantalla «Buscar partidas»: barra con filtros y resultados por relevancia. Con HTMX
    solo se repintan los resultados."""

    modulo = MODULO

    def get(self, request):
        filtros = busqueda.Filtros.de_peticion(request.GET)
        resultado = busqueda.buscar(request.user, filtros)
        sin_pagina = _sin_pagina(request)
        todas = sin_pagina.copy()
        todas["seccion"] = busqueda.TODAS
        contexto = {
            "resultado": resultado,
            "filas": _filas(resultado, request.user),
            "filtros": filtros,
            "consulta": sin_pagina.urlencode(),
            "ver_todas": todas.urlencode(),
            "puede_copiar": puede_editar(request.user, MODULO),
        }
        if request.htmx and request.htmx.target == "resultados":
            return render(request, "catalogos/_resultados_busqueda.html", contexto)
        contexto |= {
            "externos": Banco.objects.filter(tipo=Banco.EXTERNO).order_by("nombre"),
            "secciones": secciones_del_selector(),
            "unidades": unidades.CANONICAS,
            "migas": [("Banco de precios", "/banco/"), ("Buscar partidas", "")],
        }
        return render(request, "catalogos/buscar.html", contexto)


# ── Selector de partida para otras pantallas ───────────────────────────────────
#
# Uso en una plantilla (p. ej. una línea del presupuesto):
#   {% include "catalogos/_selector_partida.html" with nombre="partida"
#      unidad_destino=linea.unidad %}
# Deja un campo oculto `nombre` con el pk de la partida elegida (validarlo con
# catalogos.formularios.CampoPartida) y lanza el evento `partida-elegida` con
# {nombre, pk, codigo, unidad, misma_unidad} para quien quiera reaccionar.


def _parametros_selector(request):
    nombre = (request.GET.get("nombre") or "partida").strip()[:50]
    return nombre, (request.GET.get("unidad_destino") or "").strip()[:20]


def contexto_selector(nombre, unidad_destino, elegida=None):
    return {
        "nombre": nombre,
        "unidad_destino": unidades.unidad(unidad_destino) if unidad_destino else "",
        "elegida": elegida,
        "otra_unidad": bool(elegida and unidad_destino)
        and not unidades.misma_unidad(elegida.unidad, unidad_destino),
    }


class SelectorResultados(ConPermiso, View):
    """Resultados del selector (HTMX): las partidas de otra unidad salen marcadas."""

    modulo = MODULO

    def get(self, request):
        nombre, unidad_destino = _parametros_selector(request)
        filtros = busqueda.Filtros.de_peticion(request.GET)
        # El campo se llama «<nombre>_buscar» para no chocar con el formulario que lo contiene
        filtros.q = (request.GET.get(f"{nombre}_buscar") or filtros.q).strip()[:200]
        filtros.seccion = filtros.seccion or busqueda.TODAS
        filtros.seccion_primero = True
        resultado = busqueda.buscar(request.user, filtros, por_pagina=POR_PAGINA_SELECTOR)
        contexto = contexto_selector(nombre, unidad_destino) | {
            "resultado": resultado,
            "filas": _filas(resultado, request.user, unidad_destino) if filtros.q else [],
        }
        return render(request, "catalogos/_selector_resultados.html", contexto)


class SelectorComponente(ConPermiso, View):
    """El componente entero, con la partida elegida (`elegida=pk`) o vacío para buscar otra."""

    modulo = MODULO

    def get(self, request):
        nombre, unidad_destino = _parametros_selector(request)
        pk = request.GET.get("elegida") or ""
        elegida = None
        if pk.isdigit():
            elegida = get_object_or_404(Partida.objects.select_related("banco"), pk=pk)
        contexto = contexto_selector(nombre, unidad_destino, elegida)
        respuesta = HttpResponse(
            render_to_string("catalogos/_selector_partida.html", contexto, request=request)
        )
        if elegida is not None:
            respuesta["HX-Trigger"] = json.dumps({
                "partida-elegida": {
                    "nombre": nombre, "pk": elegida.pk, "codigo": elegida.codigo,
                    "unidad": elegida.unidad, "misma_unidad": not contexto["otra_unidad"],
                }
            })  # fmt: skip
        return respuesta
