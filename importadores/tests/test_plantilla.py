from decimal import Decimal as D

import pytest

from importadores.plantilla import leer_plantilla, normalizar, unidad_de_etiqueta
from importadores.tests.plantilla_muestra import crear_plantilla


@pytest.fixture(scope="module")
def plantilla():
    return leer_plantilla(crear_plantilla())


def _partida(resultado, nombre):
    return next(p for p in resultado.partidas if p.nombre == nombre)


def test_parametros_generales_del_indice(plantilla):
    assert {k: v for k, (_, v) in plantilla.parametros.items()} == {
        "distancia_km": D("20"), "incremento_comercial_pct": D("0"), "importe_km": D("0.4"),
        "operarios_desplazados": D("2"),
    }  # fmt: skip


def test_todas_las_hojas_de_partida_activas_y_de_biblioteca(plantilla):
    """Lección 8: se importan todas, estén o no entre Hoja1 y Hoja2; las auxiliares no."""
    assert [(p.nombre, p.activa) for p in plantilla.partidas] == [
        ("PINTURA INTERIOR", True), ("ESMALTE METAL", True), ("EFECTO PIZARRA", True),
        ("BARNIZ MADERA", False),
    ]  # fmt: skip
    assert plantilla.hojas_ignoradas == ["NOTAS"]
    assert any("«NOTAS» no tiene la forma de una partida" in a for a in plantilla.avisos)
    assert any(lec.numero == 8 and "3 hoja(s) entre Hoja1 y Hoja2" in lec.texto
               for lec in plantilla.lecciones)  # fmt: skip


def test_celdas_de_cada_partida(plantilla):
    p = _partida(plantilla, "ESMALTE METAL")
    assert (p.medicion, p.precio_mo, p.beneficio_pct) == (D("20"), D("12.9"), D("10.00"))
    assert (p.unidad, p.etiqueta_unidad) == ("m²", "M2 Obra")
    assert (p.fijaciones, p.transportes, p.coste_dia) == (D("0.5"), D("0.25"), D("189"))
    imprimacion, esmalte = p.materiales
    assert (imprimacion.nombre, imprimacion.consumo, imprimacion.merma_pct) == (
        "Imprimación antioxidante", D("0.2"), D("10.00"),
    )  # fmt: skip
    # Descuento del 10 % sobre la tarifa de 8 €
    assert (imprimacion.precio, imprimacion.descuento_pct, imprimacion.precio_neto) == (
        D("8.0"), D("10.0"), D("7.20"),
    )  # fmt: skip
    assert esmalte.precio_neto == D("10.0")
    # Materiales 0,2×1,1×7,20 + 0,15×1,1×10 = 3,23; + 0,50 + 0,25 + 12,90 de mano de obra
    assert p.coste_directo_plantilla == D("16.88")


@pytest.mark.parametrize(
    "etiqueta, unidad",
    [("M2 Obra", "m²"), ("ML Obra", "ml"), ("M3 Obra", "m³"), ("UD Obra", "ud"), ("", "m²"),
     ("Superficie", "m²")],
)  # fmt: skip
def test_unidad_propuesta_desde_la_etiqueta(etiqueta, unidad):
    assert unidad_de_etiqueta(etiqueta) == unidad


def test_coste_dia_de_la_formula_o_del_rendimiento_escrito():
    """Lección 3: el coste día va escrito dentro de la fórmula «=189/F6»; si la celda solo
    tiene el rendimiento, coste día = rendimiento × precio MO/ud."""
    con_formula = leer_plantilla(crear_plantilla())
    sin_formula = leer_plantilla(crear_plantilla(coste_dia_formula=False))
    assert con_formula.coste_dia == D("189")
    assert sin_formula.coste_dia == D("189.00")
    assert any("Coste día de 189 €" in lec.texto for lec in con_formula.lecciones)
    assert not any("Coste día" in lec.texto for lec in sin_formula.lecciones)


def test_leccion_1_los_totales_salen_de_las_hojas_no_de_los_resumenes(plantilla):
    """RESUMEN PARTIDAS arrastra una fila duplicada: 1.700 € frente a 1.300 € de las hojas
    activas (la de biblioteca no cuenta)."""
    assert plantilla.total_hojas == D("1300")
    leccion = next(lec for lec in plantilla.lecciones if lec.numero == 1)
    assert "1.700,00 €" in leccion.texto and "1.300,00 €" in leccion.texto
    assert "«PINTURA INTERIOR (2)»" in leccion.texto
    assert "PINTURA INTERIOR (2)" not in [p.nombre for p in plantilla.partidas]


def test_lecciones_2_3_4_y_6_detectadas_en_las_formulas(plantilla):
    numeros = {lec.numero: lec.texto for lec in plantilla.lecciones if lec.numero != 3}
    assert "F8" in numeros[2] and "H8" in numeros[2]  # gasto financiero con dos celdas
    assert "directo en RESUMEN PARTIDAS" in numeros[4]  # desplazamiento con dos clasificaciones
    assert "divisores fijos" in numeros[6]  # sacos y rollos
    assert any("14 %" in lec.texto for lec in plantilla.lecciones if lec.numero == 3)


def test_material_sin_consumo_o_sin_nombre_no_se_importa():
    partidas = [("UNA", 10, 2, [("Con nombre sin consumo", 1, 0, 0), ("", 1, 0, 0.3)], 0, 0, 10)]
    r = leer_plantilla(crear_plantilla(partidas=partidas, biblioteca=[]))
    assert r.partidas[0].materiales == []
    assert any("sin consumo" in a for a in r.avisos)
    assert any("sin nombre de material" in a for a in r.avisos)


def test_fichero_que_no_es_excel():
    r = leer_plantilla(b"esto no es un libro")
    assert r.errores == ["No es un libro de Excel válido (.xlsx o .xlsm)."] and not r.partidas


def test_normalizar_nombres():
    assert normalizar("  Pintura  PLÁSTICA blanca ") == normalizar("pintura plastica blanca")
