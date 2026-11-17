from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from .permisos import puede_ver

# Lanzador de módulos: nombre, icono de Bootstrap Icons, fase, url, módulo
# de permisos (core/permisos.py; None = aún sin reglas). Un módulo sin url aún no está
# construido y se muestra en gris.
MODULOS = [
    ("Presupuestos", "bi-calculator", "", None, None),
    ("Obras", "bi-building", "", None, None),
    ("Producción", "bi-rulers", "", None, None),
    ("Proformas", "bi-receipt", "", None, None),
    ("Terceros", "bi-people", "", None, None),
    ("Costes", "bi-graph-up-arrow", "", None, None),
    ("Informes", "bi-bar-chart-line", "", None, None),
    ("Ajustes", "bi-gear", "", "empresa:inicio", "ajustes"),
]


@login_required
def inicio(request):
    """Pantalla de inicio: lanzador con los módulos que permite el rol del usuario."""
    modulos = [
        (nombre, icono, fase, reverse(url) if url else None)
        for nombre, icono, fase, url, permiso in MODULOS
        if permiso is None or puede_ver(request.user, permiso)
    ]
    return render(request, "core/inicio.html", {"modulos": modulos})
