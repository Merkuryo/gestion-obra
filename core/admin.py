from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Datos para documentos", {"fields": ("telefono", "cargo")}),
    )
    list_display = ("username", "first_name", "last_name", "email", "cargo", "is_active")
