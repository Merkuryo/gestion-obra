"""Crea el banco propio y da a las plantillas de rol sus permisos en el banco de precios.

Permisos por defecto (matriz de roles): Gerencia y Oficina técnica editan; Administración y
Jefe de obra solo ven, con importes. Encargado, sin acceso. Solo se tocan los roles de serie que
aún no tienen permisos en esta parte (copiados aquí para que la migración no cambie si el código
cambia). Reversible.
"""

from django.db import migrations

E = {"ver": True, "crear": True, "editar": True, "ver_importes": True}
V = {"ver": True, "ver_importes": True}
PARTE = "banco"
PERMISOS = {"Gerencia": E, "Administración": V, "Oficina técnica": E, "Jefe de obra": V}


def adelante(apps, schema_editor):
    Banco = apps.get_model("catalogos", "Banco")
    Banco.objects.get_or_create(tipo="propio", defaults={"nombre": "Banco propio"})
    Rol = apps.get_model("core", "Rol")
    PermisoRol = apps.get_model("core", "PermisoRol")
    for rol in Rol.objects.filter(es_plantilla=True, nombre__in=PERMISOS):
        if not PermisoRol.objects.filter(rol=rol, parte=PARTE).exists():
            PermisoRol.objects.create(rol=rol, parte=PARTE, **PERMISOS[rol.nombre])


def atras(apps, schema_editor):
    apps.get_model("core", "PermisoRol").objects.filter(parte=PARTE).delete()
    Banco = apps.get_model("catalogos", "Banco")
    Banco.objects.filter(tipo="propio", partidas__isnull=True, recursos__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalogos", "0001_inicial"),
        ("core", "0005_seccion_elegida"),
    ]

    operations = [migrations.RunPython(adelante, atras)]
