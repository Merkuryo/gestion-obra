import pytest
from django.contrib.auth.models import Group

from empresa.models import Empresa


@pytest.fixture
def empresa(db):
    return Empresa.objects.create(razon_social="Construcciones Ejemplo SL", cif="B12345674")


@pytest.fixture
def usuario_con_rol(django_user_model):
    """Fábrica: usuario_con_rol("Gerencia") → usuario del grupo (sin rol si es None)."""

    def crear(rol=None):
        usuario = django_user_model.objects.create_user(
            f"u_{(rol or 'sin_rol').replace(' ', '_')}", password="x"
        )
        if rol:
            usuario.groups.add(Group.objects.get(name=rol))
        return usuario

    return crear


@pytest.fixture
def cliente_con_rol(client, usuario_con_rol):
    """Fábrica: cliente_con_rol("Gerencia") → cliente HTTP ya identificado con ese rol."""

    def crear(rol=None):
        client.force_login(usuario_con_rol(rol))
        return client

    return crear
