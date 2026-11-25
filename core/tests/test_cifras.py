from decimal import Decimal as D

import pytest

from core.cifras import decimales_precio, redondear_precio
from core.templatetags.componentes import precio_unitario


@pytest.mark.parametrize(
    "valor, decimales, redondeado",
    [
        (D("0.0120"), 3, D("0.012")), (D("2.7100"), 2, D("2.71")), (D("22.5000"), 2, D("22.50")),
        (D("23.6250"), 3, D("23.625")), (D("1.2346"), 4, D("1.2346")), (D("5"), 2, D("5.00")),
        (D("1.23456"), 4, D("1.2346")), (D("0.00005"), 4, D("0.0001")),
        (D("100.0000"), 2, D("100.00")),
    ],
)  # fmt: skip
def test_decimales_de_un_precio_unitario(valor, decimales, redondeado):
    assert decimales_precio(valor) == decimales
    assert redondear_precio(valor) == redondeado and str(redondear_precio(valor)) == str(redondeado)


def test_sin_valor():
    assert decimales_precio(None) == 2 and redondear_precio(None) is None


def test_filtro_en_pantalla(settings):
    assert precio_unitario(D("0.0120")) == "0,012"
    assert precio_unitario(D("1234.5000")).endswith("234,50")  # con separador de miles
    assert precio_unitario(None) == ""
