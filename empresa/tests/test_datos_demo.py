from decimal import Decimal

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from core.roles import GERENCIA, tiene_rol
from empresa.models import Empresa, Seccion


@pytest.fixture
def demo(settings, tmp_path, db):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_carga_empresa_secciones_y_usuario(demo):
    call_command("datos_demo")

    empresa = Empresa.actual()
    assert empresa.razon_social == "Construcciones Ejemplo SL"
    assert empresa.poblacion == "Barcelona"
    assert empresa.color_primario == "#C2410C"
    assert empresa.logo.name == "empresa/demo-logo-ce.png"
    assert (demo / "empresa" / "demo-logo-ce.png").exists()
    empresa.full_clean()  # CIF ficticio pero con dígito de control válido

    secciones = {s.codigo: s for s in Seccion.objects.all()}
    assert set(secciones) == {"ALB", "INS", "CAR", "PYL", "PT"}
    assert len({s.pct_beneficio_defecto for s in secciones.values()}) == 5
    assert secciones["PT"].pct_beneficio_defecto == Decimal("22.00")
    assert not secciones["CAR"].activa
    assert all(s.activa for c, s in secciones.items() if c != "CAR")
    for codigo in ("INS", "PT"):
        assert sum(f["pct"] for f in secciones[codigo].plantilla_fases) == 100
        secciones[codigo].full_clean()
    assert secciones["ALB"].plantilla_fases == []

    usuario = authenticate(username="demo", password="demo-capturas-2026")
    assert usuario is not None
    assert usuario.get_full_name() == "Usuario Demo"
    assert tiene_rol(usuario, GERENCIA)
    assert not usuario.is_superuser


def test_es_idempotente(demo):
    call_command("datos_demo")
    Seccion.objects.filter(codigo="ALB").update(nombre="Cambiada")
    call_command("datos_demo")

    assert Empresa.objects.count() == 1
    assert Seccion.objects.count() == 5
    assert Seccion.objects.get(codigo="ALB").nombre == "Albañilería"
    assert not Seccion.objects.get(codigo="CAR").activa
    assert get_user_model().objects.filter(username="demo").count() == 1
    assert [p.name for p in (demo / "empresa").iterdir()] == ["demo-logo-ce.png"]


def test_reactiva_y_sustituye_lo_existente(demo, empresa):
    Seccion.objects.create(codigo="CAR", nombre="Carpintería", activa=True)
    Seccion.objects.create(codigo="PT", nombre="Pintura", activa=False)

    call_command("datos_demo")

    assert not Seccion.objects.get(codigo="CAR").activa
    assert Seccion.objects.get(codigo="PT").activa
    assert Empresa.actual().color_primario == "#C2410C"


def test_no_se_ejecuta_sin_debug(settings, db):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("datos_demo")
    assert Empresa.actual() is None
    assert not get_user_model().objects.filter(username="demo").exists()
