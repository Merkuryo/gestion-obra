"""Convierte los roles fijos en roles editables.

Cada grupo existente pasa a ser un Rol. Los de serie llevan los permisos que tenían en la
matriz fija de core/permisos.py (copiada aquí tal como estaba, para que la migración no
cambie si el código cambia) y se añade la plantilla Encargado. Reversible.
"""

from django.db import migrations

E = {"ver": True, "crear": True, "editar": True, "ver_importes": True}
V = {"ver": True, "ver_importes": True}

# Matriz fija anterior a los roles editables, más la parte «roles» (Usuarios y roles:
# solo Gerencia) y las plantillas con sus permisos propios.
PLANTILLAS = {
    "Gerencia": {
        "descripcion": "Todo; aprueba y ve márgenes.",
        "permisos": {"ajustes": E, "roles": E},
        "ver_nominas": True,
        "ver_margenes": True,
    },
    "Administración": {
        "descripcion": "Terceros, proformas, cobros, pagos, costes e informes.",
        "permisos": {"ajustes": V},
        "ver_nominas": True,
        "ver_margenes": True,
    },
    "Oficina técnica": {
        "descripcion": "Presupuestos, contratos de subcontrata, mediciones y valoradas.",
        "permisos": {"ajustes": V},
        "ver_nominas": False,
        "ver_margenes": True,
    },
    "Jefe de obra": {
        "descripcion": "Todo lo de sus obras asignadas, salvo las nóminas.",
        "permisos": {},
        "ver_nominas": False,
        "ver_margenes": True,
    },
    "Encargado": {
        "descripcion": "Registra incidencias de sus obras sin ver importes.",
        "permisos": {},
        "ver_nominas": False,
        "ver_margenes": False,
    },
}
NUEVOS = ["Encargado"]  # grupos que crea esta migración (se borran al deshacerla)


def crear_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Rol = apps.get_model("core", "Rol")
    PermisoRol = apps.get_model("core", "PermisoRol")
    for nombre in NUEVOS:
        Group.objects.get_or_create(name=nombre)
    for grupo in Group.objects.all():
        datos = PLANTILLAS.get(grupo.name)
        rol = Rol.objects.create(
            grupo=grupo,
            nombre=grupo.name,
            descripcion=datos["descripcion"] if datos else "",
            es_plantilla=datos is not None,
            ver_nominas=bool(datos and datos["ver_nominas"]),
            ver_margenes=bool(datos and datos["ver_margenes"]),
        )
        for parte, acciones in (datos["permisos"] if datos else {}).items():
            PermisoRol.objects.create(rol=rol, parte=parte, **acciones)


def borrar_roles(apps, schema_editor):
    apps.get_model("core", "PermisoRol").objects.all().delete()
    apps.get_model("core", "Rol").objects.all().delete()
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=NUEVOS, user__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0003_roles_editables")]

    operations = [migrations.RunPython(crear_roles, borrar_roles)]
