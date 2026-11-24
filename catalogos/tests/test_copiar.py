from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from catalogos import servicios
from catalogos.models import LineaDescompuesto, Partida, Recurso
from empresa.models import Seccion

pytestmark = pytest.mark.django_db


@pytest.fixture
def externo(presupuesto_bc3, gerente):
    return servicios.importar_bc3(presupuesto_bc3, "presupuesto_2016.bc3", gerente).banco


def _partida(banco, codigo):
    return Partida.objects.get(banco=banco, codigo=codigo)


def test_copia_con_rendimientos_auxiliares_y_recursos(externo, gerente):
    pt = Seccion.objects.create(codigo="PT", nombre="Pintura")
    copia = servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente, seccion=pt)
    propio = servicios.banco_propio()

    assert copia.banco == propio and copia.codigo == "ALB010" and copia.seccion == pt
    assert copia.origen == "Reforma de baño de ejemplo · ALB010" and not copia.auxiliar
    # Los rendimientos se mantienen
    assert [(ln.elemento.codigo, ln.rendimiento) for ln in copia.lineas.all()] == [
        ("MO1", D("0.2500")), ("MT1", D("1.0500")), ("AX1", D("0.0100")),
    ]  # fmt: skip
    auxiliar = _partida(propio, "AX1")
    assert auxiliar.auxiliar and auxiliar.lineas.count() == 3
    pendientes = dict(Recurso.objects.filter(banco=propio).values_list("codigo", "pendiente"))
    assert pendientes == {
        "MO1": "coste_hora", "MO2": "coste_hora",  # mano de obra: coste hora de la empresa
        "MT1": "tarifa", "MT4": "tarifa",  # material: artículo y tarifa
        "MQ1": "",
    }  # fmt: skip
    # Precio desde el descompuesto: 22,50×0,25 + 1,20×1,05 + (19,80×1,5 + 98×0,25 + 2,10×0,5)×0,01
    assert auxiliar.precio == D("55.2500") and copia.precio == D("7.4375")
    # El banco externo no cambia
    assert _partida(externo, "ALB010").precio == D("12.4000")


def test_segunda_copia_reutiliza_recursos_y_auxiliares(externo, gerente):
    servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente)
    servicios.copiar_al_banco_propio(_partida(externo, "ALB020"), gerente)
    propio = servicios.banco_propio()
    assert Partida.objects.filter(banco=propio).count() == 3  # ALB010, ALB020 y AX1 una vez
    assert Recurso.objects.filter(banco=propio).count() == 6  # MO1, MO2, MT1, MT2, MT4, MQ1


def test_copiar_otra_vez_da_codigo_nuevo(externo, gerente):
    servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente)
    otra = servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente)
    assert otra.codigo == "ALB010-2"
    assert Partida.objects.filter(banco=otra.banco, codigo="AX1").count() == 1


def test_la_copia_es_independiente(externo, gerente):
    copia = servicios.copiar_al_banco_propio(_partida(externo, "PIN010"), gerente)
    mo3 = Recurso.objects.get(banco=copia.banco, codigo="MO3")
    mo3.precio = D("30.0000")
    servicios.guardar_recurso(mo3, gerente)
    copia.refresh_from_db()
    # 30×0,12 + 4,10×1,1×0,3 = 3,60 + 1,353
    assert copia.precio == D("4.9530")
    assert Recurso.objects.get(banco=externo, codigo="MO3").precio == D("21.0000")


def test_no_se_copia_lo_que_ya_es_propio(gerente):
    propia = Partida.objects.create(banco=servicios.banco_propio(), codigo="X", resumen="X")
    with pytest.raises(ValidationError):
        servicios.copiar_al_banco_propio(propia, gerente)


def test_cambiar_un_recurso_recalcula_tambien_las_que_usan_su_auxiliar(externo, gerente):
    copia = servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente)
    mt4 = Recurso.objects.get(banco=copia.banco, codigo="MT4")
    mt4.precio = D("100.0000")  # +2 × 0,25 = +0,50 en AX1 → +0,005 en ALB010
    servicios.guardar_recurso(mt4, gerente)
    copia.refresh_from_db()
    assert _partida(copia.banco, "AX1").precio == D("55.7500")
    assert copia.precio == D("7.4425")


def test_el_descompuesto_no_puede_contener_a_la_propia_partida(externo, gerente):
    copia = servicios.copiar_al_banco_propio(_partida(externo, "ALB010"), gerente)
    auxiliar = _partida(copia.banco, "AX1")
    ciclo = LineaDescompuesto(auxiliar=copia, rendimiento=D("1"))
    with pytest.raises(ValidationError, match="propia partida"):
        servicios.guardar_partida(auxiliar, [ciclo], [], gerente)


def test_los_bancos_externos_son_de_solo_lectura(externo, gerente):
    partida = _partida(externo, "ALB010")
    with pytest.raises(ValidationError, match="solo lectura"):
        servicios.guardar_partida(partida, [], [], gerente)
    with pytest.raises(ValidationError, match="solo lectura"):
        servicios.guardar_recurso(Recurso.objects.get(banco=externo, codigo="MO1"), gerente)
