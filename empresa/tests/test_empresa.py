from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from empresa.models import Empresa, Seccion
from empresa.servicios import obtener_empresa, validar_pct_desvio


def test_empresa_es_unica(empresa):
    with pytest.raises(ValidationError):
        Empresa(razon_social="Otra SL", cif="B12345674").save()
    assert Empresa.objects.count() == 1
    assert Empresa.actual().pk == Empresa.PK_UNICA


def test_valores_por_defecto(empresa):
    assert empresa.jornada_horas == Decimal("8.00")
    assert empresa.pct_desvio_defecto == Decimal("2.00")
    assert empresa.pct_desvio_tope == Decimal("4.00")
    assert empresa.recargo_festivos == Decimal("50.00")
    assert (empresa.ancho_cliente, empresa.ancho_obra) == (3, 4)


def test_desvio_por_defecto_no_supera_tope(empresa):
    empresa.pct_desvio_defecto = Decimal("5")
    with pytest.raises(ValidationError):
        empresa.full_clean()


def test_cif_invalido(db):
    with pytest.raises(ValidationError):
        Empresa(razon_social="X", cif="B12345675").full_clean()


def test_anchos_minimos(empresa):
    empresa.ancho_cliente = 2
    with pytest.raises(ValidationError):
        empresa.full_clean()


@pytest.mark.parametrize(
    "pct, valido",
    [("0", True), ("2", True), ("4", True), ("4.01", False), ("-1", False)],
)
def test_validar_pct_desvio(empresa, pct, valido):
    if valido:
        assert validar_pct_desvio(Decimal(pct)) == Decimal(pct)
    else:
        with pytest.raises(ValidationError):
            validar_pct_desvio(Decimal(pct))


def test_obtener_empresa_sin_configurar(db):
    with pytest.raises(Empresa.DoesNotExist):
        obtener_empresa()


def test_historial(empresa):
    empresa.telefono = "930000000"
    empresa.save()
    assert empresa.history.count() == 2


class TestSeccion:
    def test_codigo_valido(self, db):
        Seccion(codigo="PyL", nombre="Pladur").full_clean()

    @pytest.mark.parametrize("codigo", ["PINTU", "P-T", ""])
    def test_codigo_no_valido(self, db, codigo):
        with pytest.raises(ValidationError):
            Seccion(codigo=codigo, nombre="X").full_clean()

    def test_fases_suman_100(self, db):
        fases = [
            {"nombre": "Imprimación", "pct": 30},
            {"nombre": "1.ª mano", "pct": 35},
            {"nombre": "2.ª mano", "pct": 35},
        ]
        Seccion(codigo="PT", nombre="Pintura", plantilla_fases=fases).full_clean()

    def test_fases_que_no_suman_100(self, db):
        with pytest.raises(ValidationError):
            Seccion(
                codigo="PT", nombre="Pintura", plantilla_fases=[{"nombre": "Única", "pct": 90}]
            ).full_clean()
