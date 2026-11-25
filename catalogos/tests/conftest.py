from pathlib import Path

import pytest

MUESTRAS = Path(__file__).resolve().parents[2] / "importadores" / "tests" / "muestras"


@pytest.fixture
def presupuesto_bc3():
    return (MUESTRAS / "presupuesto_2016.bc3").read_bytes()


@pytest.fixture
def banco_850_bc3():
    return (MUESTRAS / "banco_2007_850.bc3").read_bytes()


@pytest.fixture
def gerente(django_user_model):
    """Quien importa y copia en los tests (otro usuario que el de cliente_con_rol)."""
    from django.contrib.auth.models import Group

    usuario = django_user_model.objects.create_user("gerente_banco", password="x")
    usuario.groups.add(Group.objects.get(name="Gerencia"))
    return usuario


@pytest.fixture
def media_temporal(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path
