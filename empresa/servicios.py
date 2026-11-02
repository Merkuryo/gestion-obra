"""Lógica de negocio de empresa. Las vistas llaman aquí; nunca calculan por su cuenta."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import Empresa


def obtener_empresa() -> Empresa:
    """Devuelve la empresa o lanza un error claro si no está configurada."""
    empresa = Empresa.actual()
    if empresa is None:
        raise Empresa.DoesNotExist(
            "La empresa no está configurada. Es lo primero que se da de alta."
        )
    return empresa


def validar_pct_desvio(pct: Decimal, empresa: Empresa | None = None) -> Decimal:
    """Comprueba que un % de desvío (imprevistos) no supere el tope de la empresa. Devuelve el valor."""
    empresa = empresa or obtener_empresa()
    if pct < 0 or pct > empresa.pct_desvio_tope:
        raise ValidationError(f"El desvío debe estar entre 0 y {empresa.pct_desvio_tope} %.")
    return pct


def guardar_empresa(empresa: Empresa, usuario) -> Empresa:
    """Guarda la empresa (alta o edición) con quién y cuándo; historial: simple-history."""
    if empresa._state.adding:
        empresa.creado_por = usuario
    empresa.modificado_por = usuario
    empresa.save()
    return empresa
