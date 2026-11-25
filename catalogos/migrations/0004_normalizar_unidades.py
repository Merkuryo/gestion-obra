from django.db import migrations


def normalizar(apps, schema_editor):
    """Unidades ya guardadas a su forma normalizada (m2 → m², M.L. → ml…). Las que no se
    reconocen se quedan como estaban, sin espacios de más."""
    from core.unidades import unidad

    for nombre in ("Partida", "Recurso"):
        modelo = apps.get_model("catalogos", nombre)
        cambiados = []
        for objeto in modelo.objects.only("pk", "unidad").iterator(chunk_size=2000):
            nueva = unidad(objeto.unidad)[:20]
            if nueva != objeto.unidad:
                objeto.unidad = nueva
                cambiados.append(objeto)
        modelo.objects.bulk_update(cambiados, ["unidad"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [("catalogos", "0003_partida_precio_mo_referencia")]

    operations = [migrations.RunPython(normalizar, migrations.RunPython.noop)]
