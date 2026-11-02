from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Empresa, Seccion


@admin.register(Empresa)
class EmpresaAdmin(SimpleHistoryAdmin):
    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "razon_social",
                    "nombre_comercial",
                    "cif",
                    "direccion",
                    "cp",
                    "poblacion",
                    "provincia",
                    "telefono",
                    "email",
                    "web",
                )
            },
        ),
        ("Imagen y documentos", {"fields": ("logo", "color_primario", "iban", "pie_legal")}),
        (
            "Códigos analíticos (fijos tras la instalación)",
            {
                "fields": ("ancho_cliente", "ancho_obra"),
            },
        ),
        (
            "Parámetros económicos",
            {
                "fields": (
                    "pct_desvio_defecto",
                    "pct_desvio_tope",
                    "jornada_horas",
                    "recargo_festivos",
                    "dias_aviso_pago",
                    "umbral_aviso_coste_hora",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return Empresa.actual() is None and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Seccion)
class SeccionAdmin(SimpleHistoryAdmin):
    list_display = ("codigo", "nombre", "pct_beneficio_defecto", "activa")
    list_filter = ("activa",)
