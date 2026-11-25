import importlib

from django.db import migrations


def normalizar(apps, schema_editor):
    """«m» pasa a ser metro lineal («ml»): se vuelven a normalizar las unidades guardadas."""
    importlib.import_module("catalogos.migrations.0004_normalizar_unidades").normalizar(
        apps, schema_editor
    )


class Migration(migrations.Migration):
    dependencies = [("catalogos", "0004_normalizar_unidades")]

    operations = [migrations.RunPython(normalizar, migrations.RunPython.noop)]
