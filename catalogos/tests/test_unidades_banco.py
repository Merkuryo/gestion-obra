import importlib

import pytest
from django.apps import apps

from catalogos import servicios
from catalogos.models import Banco, Partida, Recurso

pytestmark = pytest.mark.django_db


def test_migracion_normaliza_las_unidades_ya_guardadas():
    propio = servicios.banco_propio()
    externo = Banco.objects.create(tipo=Banco.EXTERNO, nombre="Externo", clave="x")
    # Guardadas como estaban antes (sin pasar por los servicios)
    Partida.objects.bulk_create([
        Partida(banco=propio, codigo="A", unidad="M2", resumen="a"),
        Partida(banco=externo, codigo="B", unidad="m.l.", resumen="b"),
        Partida(banco=externo, codigo="C", unidad=" saco ", resumen="c"),
    ])  # fmt: skip
    Recurso.objects.bulk_create([Recurso(banco=propio, codigo="R", unidad="UD", resumen="r")])
    migracion = importlib.import_module("catalogos.migrations.0004_normalizar_unidades")
    migracion.normalizar(apps, None)
    assert dict(Partida.objects.values_list("codigo", "unidad")) == {
        "A": "m²", "B": "ml", "C": "saco",
    }  # fmt: skip
    assert Recurso.objects.get(codigo="R").unidad == "ud"


def test_guardar_a_mano_normaliza(gerente):
    partida = servicios.guardar_partida(
        Partida(codigo="X", unidad="M3", resumen="x"), [], [], gerente
    )
    recurso = servicios.guardar_recurso(Recurso(codigo="Y", unidad="Kg", resumen="y"), gerente)
    assert (partida.unidad, recurso.unidad) == ("m³", "kg")
