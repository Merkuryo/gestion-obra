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
        Seccion(codigo="ALB", nombre="Albañilería").full_clean()

    @pytest.mark.parametrize("codigo", ["INSTA", "I-N", "", "ins", "InS"])
    def test_codigo_no_valido(self, db, codigo):
        with pytest.raises(ValidationError):
            Seccion(codigo=codigo, nombre="X").full_clean()

    def test_codigo_inmutable_una_vez_creada(self, db):
        s = Seccion.objects.create(codigo="INS", nombre="Instalaciones")
        s.codigo = "IX"
        with pytest.raises(ValidationError):
            s.save()
        assert Seccion.objects.get(pk=s.pk).codigo == "INS"

    def test_se_puede_editar_lo_demas_sin_tocar_el_codigo(self, db):
        s = Seccion.objects.create(codigo="ALB", nombre="Albañilería")
        s.nombre = "Albañilería y reformas"
        s.save()
        assert Seccion.objects.get(pk=s.pk).nombre == "Albañilería y reformas"

    @pytest.mark.parametrize(
        "codigo, nombre, fases",
        [
            (
                "INS",
                "Instalaciones",
                [("Replanteo", 10), ("Tendido", 40), ("Conexionado", 30), ("Pruebas", 20)],
            ),
            ("ALB", "Albañilería", [("Replanteo", 10), ("Ejecución", 75), ("Remates", 15)]),
            ("PT", "Pintura", [("Imprimación", 30), ("1.ª mano", 35), ("2.ª mano", 35)]),
        ],
    )
    def test_fases_de_varios_oficios_suman_100(self, db, codigo, nombre, fases):
        plantilla = [{"nombre": n, "pct": p} for n, p in fases]
        Seccion(codigo=codigo, nombre=nombre, plantilla_fases=plantilla).full_clean()

    def test_seccion_sin_fases_es_valida(self, db):
        s = Seccion.objects.create(codigo="CAR", nombre="Carpintería")
        assert s.plantilla_fases == []
        s.full_clean()

    def test_fases_que_no_suman_100(self, db):
        with pytest.raises(ValidationError):
            Seccion(
                codigo="INS",
                nombre="Instalaciones",
                plantilla_fases=[{"nombre": "Única", "pct": 90}],
            ).full_clean()
