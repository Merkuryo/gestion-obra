from decimal import Decimal as D

from catalogos.calculos import cantidad_por_unidad, importe_linea, precio_partida, redondear_d4


def test_cantidad_con_factor_y_merma():
    # 1,1 × 0,30 L × (1 + 5 %) = 0,3465 L por m²
    assert cantidad_por_unidad(D("1.1"), D("0.30"), D("5")) == D("0.3465")


def test_importe_de_una_linea():
    assert importe_linea(D("1"), D("0.25"), D("0"), D("22.50")) == D("5.6250")
    assert importe_linea(D("1"), D("0.25"), D("0"), None) is None


def test_precio_de_la_partida_suma_sus_lineas():
    lineas = [
        (D("1"), D("0.25"), D("0"), D("22.50")),  # mano de obra: 5,625
        (D("1"), D("1.05"), D("0"), D("1.20")),  # material: 1,26
        (D("1"), D("0.01"), D("0"), D("55.25")),  # auxiliar: 0,5525
    ]
    assert precio_partida(lineas) == D("7.4375")


def test_lineas_sin_precio_cuentan_cero_y_sin_lineas_no_hay_precio():
    assert precio_partida([(D("1"), D("2"), D("0"), None), (D("1"), D("1"), D("0"), D("3"))]) == D(
        "3.0000"
    )
    assert precio_partida([]) is None


def test_redondeo_a_cuatro_decimales():
    assert redondear_d4(D("0.00125")) == (D("0.0013"), True)
    assert redondear_d4(D("12.4")) == (D("12.4000"), False)
    assert redondear_d4(None) == (None, False)


# ── Mano de obra: doble entrada precio por unidad ↔ rendimiento ────────────────


def test_doble_entrada_de_la_mano_de_obra_con_el_coste_dia_de_la_plantilla():
    from catalogos import calculos

    # 189 € al día y 2,10 €/m² de mano de obra → 90 m² por día-operario
    assert calculos.rendimiento_mo(D("189"), D("2.10")) == D("90.0000")
    assert calculos.precio_mo_ud(D("189"), D("90")) == D("2.1000")
    # En el descompuesto: 8 h / 90 m² = 0,0889 h/m², a 189 / 8 = 23,625 €/h
    assert calculos.horas_por_unidad(D("8"), D("189"), D("2.10")) == D("0.0889")
    assert calculos.coste_hora_de_dia(D("189"), D("8")) == D("23.6250")
    assert calculos.coste_dia(D("23.625"), D("8")) == D("189.00")
    # 12,90 €/m²: 14,6512 m²/día y 0,5460 h/m²
    assert calculos.rendimiento_mo(D("189"), D("12.90")) == D("14.6512")
    assert calculos.horas_por_unidad(D("8"), D("189"), D("12.90")) == D("0.5460")
