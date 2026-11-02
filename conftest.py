import pytest

from empresa.models import Empresa


@pytest.fixture
def empresa(db):
    return Empresa.objects.create(razon_social="Pinturas Demo SL", cif="B12345674")
