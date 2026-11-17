import pytest
from django.core.exceptions import ValidationError

from core.validadores import es_nif_cif_valido, validar_nif_cif


@pytest.mark.parametrize(
    "valor",
    [
        "12345678Z",  # DNI
        "X1234567L",  # NIE
        "B12345674",  # CIF de sociedad limitada (control numérico)
        "b-1234567-4",  # con guiones y minúsculas
        "Q1234567D",  # organismo público ficticio (control con letra)
    ],
)
def test_documentos_validos(valor):
    assert es_nif_cif_valido(valor)


@pytest.mark.parametrize("valor", ["12345678A", "X1234567A", "B12345675", "", "ABC", "Q1234567A"])
def test_documentos_no_validos(valor):
    assert not es_nif_cif_valido(valor)


def test_validar_lanza_error():
    with pytest.raises(ValidationError):
        validar_nif_cif("B12345675")
