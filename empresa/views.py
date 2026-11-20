from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.listas import Columna, preparar_lista
from core.permisos import ConPermiso, puede_editar

from . import servicios
from .formularios import EmpresaForm, SeccionForm
from .models import Empresa, Seccion

MIGAS_EMPRESA = [("Ajustes", "/ajustes/"), ("Empresa", "")]


class FichaEmpresa(ConPermiso, View):
    """Alta y ficha de la empresa. Gerencia edita; Administración y Oficina técnica solo ven."""

    modulo = "ajustes"

    def _pintar(self, request, form):
        return render(
            request,
            "empresa/ficha.html",
            {"form": form, "ficha": Empresa.actual(), "migas": MIGAS_EMPRESA},
        )

    def get(self, request):
        empresa = Empresa.actual()
        editable = puede_editar(request.user, self.modulo)
        if empresa is None and not editable:
            return self._pintar(request, None)  # nada que mostrar: aviso, sin formulario
        return self._pintar(
            request, EmpresaForm(instance=empresa, solo_lectura=not editable, usuario=request.user)
        )

    def post(self, request):
        form = EmpresaForm(
            request.POST, request.FILES, instance=Empresa.actual(), usuario=request.user
        )
        if form.is_valid():
            quitar_logo = form.cleaned_data.get("quitar_logo", False)
            servicios.guardar_empresa(form.save(commit=False), request.user, quitar_logo)
            messages.success(request, "Datos de la empresa guardados.")
            return redirect("empresa:ficha")
        return self._pintar(request, form)


def _estado_seccion(seccion):
    return ("Activa", "success") if seccion.activa else ("Archivada", "secondary")


COLUMNAS_SECCIONES = [
    Columna("codigo", "Código"),
    Columna("nombre", "Nombre"),
    Columna("pct_beneficio_defecto", "% beneficio", numerica=True),
    Columna("activa", "Estado", tipo="badge", valor=_estado_seccion),
]


class ListaSecciones(ConPermiso, View):
    modulo = "ajustes"

    def get(self, request):
        lista = preparar_lista(
            request,
            Seccion.objects.all(),
            COLUMNAS_SECCIONES,
            buscar_en=("codigo", "nombre"),
            url_fila=lambda s: reverse("empresa:seccion", args=[s.pk]),
        )
        migas = [("Ajustes", "/ajustes/"), ("Secciones", "")]
        return render(request, "empresa/secciones_lista.html", {"lista": lista, "migas": migas})


class FichaSeccion(ConPermiso, View):
    """Alta (sin pk) y ficha de una sección. Gerencia edita; el resto solo ve."""

    modulo = "ajustes"

    def _pintar(self, request, form, seccion):
        url_lista = reverse("empresa:secciones")
        acciones = []
        if seccion and not form.solo_lectura:
            if seccion.activa:
                accion = ("archivar", "Archivar", "bi-archive", "btn-outline-danger")
            else:
                accion = ("reactivar", "Reactivar", "bi-arrow-repeat", "btn-outline-success")
            destino, texto, icono, clase = accion
            url = reverse(f"empresa:seccion_{destino}", args=[seccion.pk])
            acciones.append(
                {"texto": texto, "icono": icono, "clase": clase, "post": True, "url": url}
            )
        migas = [("Ajustes", "/ajustes/"), ("Secciones", url_lista), (str(seccion or "Nueva"), "")]
        contexto = {
            "form": form,
            "seccion": seccion,
            "acciones": acciones,
            "url_lista": url_lista,
            "migas": migas,
        }
        return render(request, "empresa/seccion_form.html", contexto)

    def get(self, request, pk=None):
        seccion = get_object_or_404(Seccion, pk=pk) if pk else None
        editable = puede_editar(request.user, self.modulo)
        if seccion is None and not editable:
            raise PermissionDenied
        form = SeccionForm(instance=seccion, solo_lectura=not editable, usuario=request.user)
        return self._pintar(request, form, seccion)

    def post(self, request, pk=None):
        seccion = get_object_or_404(Seccion, pk=pk) if pk else None
        form = SeccionForm(request.POST, instance=seccion, usuario=request.user)
        if form.is_valid():
            guardada = servicios.guardar_seccion(form.save(commit=False), request.user)
            messages.success(request, f"Sección {guardada.codigo} guardada.")
            return redirect("empresa:secciones")
        return self._pintar(request, form, seccion)


class CambiarEstadoSeccion(ConPermiso, View):
    """Archivar o reactivar (nunca borrar). Solo POST."""

    modulo = "ajustes"
    http_method_names = ["post"]
    accion = None  # "archivar" | "reactivar", lo fija la URL

    def post(self, request, pk):
        seccion = get_object_or_404(Seccion, pk=pk)
        funcion = {
            "archivar": servicios.archivar_seccion,
            "reactivar": servicios.reactivar_seccion,
        }[self.accion]
        try:
            funcion(seccion, request.user)
        except ValidationError as error:
            messages.warning(request, error.messages[0])
        else:
            verbo = "archivada" if self.accion == "archivar" else "reactivada"
            messages.success(request, f"Sección {seccion.codigo} {verbo}.")
        return redirect("empresa:secciones")
