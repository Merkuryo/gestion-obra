"""«Ver más» del historial de una ficha (respuesta parcial para HTMX)."""

from django.apps import apps
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_datetime
from django.views import View

from .historial import historial
from .permisos import puede_ver_historial


class MasHistorial(LoginRequiredMixin, View):
    def get(self, request, app, modelo, pk):
        try:
            clase = apps.get_model(app, modelo)
        except LookupError as error:
            raise Http404 from error
        if not getattr(clase, "parte_permisos", None) or not hasattr(clase, "history"):
            raise Http404
        objeto = get_object_or_404(clase, pk=pk)
        if not puede_ver_historial(request.user, objeto):
            raise PermissionDenied
        try:
            antes = parse_datetime(request.GET.get("antes", ""))
        except ValueError:
            antes = None
        if antes is None:
            raise Http404
        pagina = historial(objeto, request.user, antes=antes)
        return render(request, "componentes/_historial_filas.html", {"pagina": pagina})
