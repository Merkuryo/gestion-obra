from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

# Lanzador de módulos de gestión: nombre, icono de Bootstrap Icons, fase, url.
# Un módulo sin url aún no está construido y se muestra en gris.
MODULOS = [
    ("Presupuestos", "bi-calculator", "", None),
    ("Obras", "bi-building", "", None),
    ("Producción", "bi-rulers", "", None),
    ("Proformas", "bi-receipt", "", None),
    ("Terceros", "bi-people", "", None),
    ("Costes", "bi-graph-up-arrow", "", None),
    ("Informes", "bi-bar-chart-line", "", None),
    ("Ajustes", "bi-gear", "", "admin:index"),
]


@login_required
def inicio(request):
    modulos = [(n, i, f, reverse(u) if u else None) for n, i, f, u in MODULOS]
    return render(request, "core/inicio.html", {"modulos": modulos})
