"""Pantalla de prueba (solo en los tests) que usa el selector de partida como lo hará el
presupuesto: un formulario con una línea en «ml» y el campo `partida`."""

from django import forms
from django.http import HttpResponse
from django.template import engines
from django.urls import path

from catalogos.formularios import CampoPartida
from config.urls import urlpatterns as urls_de_la_app
from core import unidades

PLANTILLA = """{% extends "base.html" %}{% block contenido %}
<form method="post">{% csrf_token %}
  {% include "catalogos/_selector_partida.html" with nombre="partida" unidad_destino="ml" %}
  <button>Asignar</button>
</form>{% endblock %}"""


class LineaForm(forms.Form):
    partida = CampoPartida()


def prueba(request):
    if request.method == "POST":
        form = LineaForm(request.POST)
        if not form.is_valid():
            return HttpResponse(" ".join(form.errors["partida"]), status=400)
        partida = form.cleaned_data["partida"]
        misma = "misma unidad" if unidades.misma_unidad(partida.unidad, "ml") else "otra unidad"
        return HttpResponse(f"Elegida: {partida.codigo} ({misma})")
    return HttpResponse(engines["django"].from_string(PLANTILLA).render({}, request))


urlpatterns = [*urls_de_la_app, path("prueba/selector/", prueba)]
