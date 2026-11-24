"""Cambio de la sección de trabajo desde el selector de la barra superior."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from empresa.models import Seccion

from . import servicios

EVENTO = "seccion-cambiada"  # las páginas con listas filtradas por sección se recargan


def _destino(request):
    siguiente = request.POST.get("siguiente", "")
    seguro = url_has_allowed_host_and_scheme(
        siguiente, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return siguiente if siguiente and seguro else "/"


class ElegirSeccion(LoginRequiredMixin, View):
    """POST `seccion` = pk de una sección activa, o vacío para «Todas».

    Con HTMX devuelve el selector actualizado y lanza el evento `seccion-cambiada`, sin salir
    de la página. Sin JavaScript vuelve a la página de la que venía (`siguiente`).
    """

    http_method_names = ["post"]

    def post(self, request):
        valor = request.POST.get("seccion", "")
        seccion = None
        if valor:
            if not valor.isdigit():
                return HttpResponseBadRequest("Sección no válida.")
            seccion = Seccion.objects.filter(pk=valor).first()
            if seccion is None:
                return HttpResponseBadRequest("Sección no válida.")
        try:
            servicios.elegir_seccion(request.user, seccion)
        except ValidationError as error:
            return HttpResponseBadRequest(error.messages[0])

        destino = _destino(request)
        if not request.htmx:
            return redirect(destino)
        respuesta = render(request, "componentes/_selector_seccion.html", {"siguiente": destino})
        respuesta["HX-Trigger"] = EVENTO
        return respuesta
