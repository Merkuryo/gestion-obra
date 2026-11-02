from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from core.permisos import ConPermiso, puede_editar

from . import servicios
from .formularios import EmpresaForm
from .models import Empresa

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
        return self._pintar(request, EmpresaForm(instance=empresa, solo_lectura=not editable))

    def post(self, request):
        form = EmpresaForm(request.POST, request.FILES, instance=Empresa.actual())
        if form.is_valid():
            servicios.guardar_empresa(form.save(commit=False), request.user)
            messages.success(request, "Datos de la empresa guardados.")
            return redirect("empresa:ficha")
        return self._pintar(request, form)
