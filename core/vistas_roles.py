"""Ajustes → Roles: lista, alta, edición, duplicado y usuarios de cada rol.

Solo para quien gestiona roles (core.permisos.ConGestionRoles). La lógica está en
core/servicios.py.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from . import servicios
from .formularios import PermisosRolForm, RolForm
from .listas import Columna, preparar_lista
from .models import Rol
from .permisos import ConGestionRoles


def _estado(rol):
    return ("Activo", "success") if rol.activo else ("Inactivo", "secondary")


def _tipo(rol):
    return ("De serie", "primary") if rol.es_plantilla else ("Propio", "light")


COLUMNAS = [
    Columna("nombre", "Rol"),
    Columna("descripcion", "Descripción"),
    Columna("es_plantilla", "Tipo", tipo="badge", valor=_tipo),
    Columna("num_usuarios", "Usuarios", numerica=True, decimales=0),
    Columna("activo", "Estado", tipo="badge", valor=_estado),
]


class ListaRoles(ConGestionRoles, View):
    def get(self, request):
        roles = Rol.objects.annotate(num_usuarios=Count("grupo__user"))
        lista = preparar_lista(
            request,
            roles,
            COLUMNAS,
            buscar_en=("nombre", "descripcion"),
            url_fila=lambda r: reverse("rol", args=[r.pk]),
        )
        migas = [("Ajustes", "/ajustes/"), ("Roles", "")]
        return render(request, "core/roles_lista.html", {"lista": lista, "migas": migas})


class FichaRol(ConGestionRoles, View):
    """Alta (sin pk) y edición de un rol con su tabla de permisos y sus usuarios."""

    def _pintar(self, request, form, permisos_form, rol):
        url_lista = reverse("roles")
        acciones = []
        if rol:
            acciones.append(
                {
                    "texto": "Duplicar",
                    "icono": "bi-copy",
                    "post": True,
                    "url": reverse("rol_duplicar", args=[rol.pk]),
                }
            )
        migas = [("Ajustes", "/ajustes/"), ("Roles", url_lista), (str(rol or "Nuevo rol"), "")]
        contexto = {
            "form": form,
            "permisos_form": permisos_form,
            "rol": rol,
            "acciones": acciones,
            "url_lista": url_lista,
            "migas": migas,
        }
        return render(request, "core/rol_form.html", contexto)

    def get(self, request, pk=None):
        rol = get_object_or_404(Rol, pk=pk) if pk else None
        permisos = servicios.permisos_de(rol) if rol else {}
        return self._pintar(request, RolForm(instance=rol), PermisosRolForm(permisos=permisos), rol)

    def post(self, request, pk=None):
        rol = get_object_or_404(Rol, pk=pk) if pk else None
        form = RolForm(request.POST, instance=rol)
        permisos_form = PermisosRolForm(request.POST)
        if form.is_valid() and permisos_form.is_valid():
            try:
                guardado = servicios.guardar_rol(
                    form.save(commit=False),
                    permisos_form.como_dict(),
                    set(form.cleaned_data["usuarios"]),
                    request.user,
                )
            except ValidationError as error:
                if hasattr(error, "error_dict"):
                    for campo, errores in error.message_dict.items():
                        form.add_error(campo, errores)
                else:
                    form.add_error(None, error.messages)
            else:
                messages.success(request, f"Rol «{guardado}» guardado.")
                return redirect("roles")
        return self._pintar(request, form, permisos_form, rol)


class DuplicarRol(ConGestionRoles, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        copia = servicios.duplicar_rol(get_object_or_404(Rol, pk=pk), request.user)
        messages.success(request, f"Rol duplicado como «{copia}». Revisa sus permisos y usuarios.")
        return redirect("rol", pk=copia.pk)
