from django.db import migrations

ROLES = ["Gerencia", "Administración", "Oficina técnica", "Jefe de obra"]


def crear_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in ROLES:
        Group.objects.get_or_create(name=nombre)


def borrar_roles(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(crear_roles, borrar_roles)]
