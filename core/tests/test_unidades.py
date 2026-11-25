import pytest

from core.textos import contar, enumerar
from core.unidades import CANONICAS, misma_unidad, no_reconocidas, normalizar_unidad, unidad


@pytest.mark.parametrize(
    "texto, canonica",
    [
        ("m2", "m²"), ("M2", "m²"), ("m²", "m²"), ("M²", "m²"), ("m 2", "m²"), ("m2.", "m²"),
        ("m3", "m³"), ("M3", "m³"), ("m³", "m³"),
        ("ml", "ml"), ("ML", "ml"), ("m.l.", "ml"), ("M.L.", "ml"), ("m l", "ml"),
        ("m", "m"), ("M", "m"),
        ("ud", "ud"), ("u", "ud"), ("UD", "ud"), ("Ud.", "ud"), ("uds", "ud"), ("U", "ud"),
        ("h", "h"), ("H", "h"), ("hora", "h"),
        ("kg", "kg"), ("Kg", "kg"), ("l", "l"), ("L", "l"), ("lt", "l"),
        ("d", "d"), ("día", "d"), ("DIA", "d"),
        ("t", "t"), ("pa", "pa"), ("P.A.", "pa"), ("%", "%"), ("€", "€"), ("mes", "mes"),
        ("km", "km"),
    ],
)  # fmt: skip
def test_equivalencias(texto, canonica):
    assert normalizar_unidad(texto) == (canonica, True)


def test_las_canonicas_se_reconocen_a_si_mismas():
    for canonica in CANONICAS:
        assert normalizar_unidad(canonica) == (canonica, True)


def test_no_reconocida_se_guarda_tal_cual_sin_espacios_de_mas():
    assert normalizar_unidad("  saco  25kg ") == ("saco 25kg", False)
    assert normalizar_unidad("") == ("", False)
    assert unidad("Rollo") == "Rollo"


def test_misma_unidad():
    assert misma_unidad("m2", "M²") and misma_unidad("ML", "m.l.") and misma_unidad("u", "UD")
    assert not misma_unidad("m", "ml") and not misma_unidad("m²", "m³")
    assert misma_unidad("Saco", "saco")


def test_no_reconocidas_sin_repetir():
    assert no_reconocidas(["m2", "saco", "Saco ", "saco", "", "rollo"]) == ["saco", "Saco", "rollo"]


def test_contar_y_enumerar():
    assert contar(1, "partida nueva", "partidas nuevas") == "1 partida nueva"
    assert contar(2, "partida nueva", "partidas nuevas") == "2 partidas nuevas"
    assert contar(0, "partida nueva", "partidas nuevas") == "0 partidas nuevas"
    assert enumerar([]) == "" and enumerar(["a"]) == "a" and enumerar(["a", "b"]) == "a y b"
    assert enumerar(["a", "b", "c"]) == "a, b y c"
