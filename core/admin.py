from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from simple_history.admin import SimpleHistoryAdmin

from .models import PermisoRol, Rol, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Datos para documentos", {"fields": ("telefono", "cargo")}),
    )
    list_display = ("username", "first_name", "last_name", "email", "cargo", "is_active")


class PermisoRolInline(admin.TabularInline):
    model = PermisoRol
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Rol)
class RolAdmin(SimpleHistoryAdmin):
    """Solo consulta e historial: los roles se editan en Ajustes → Roles (con salvaguardas)."""

    list_display = ("nombre", "es_plantilla", "activo", "ver_nominas", "ver_margenes")
    inlines = [PermisoRolInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
