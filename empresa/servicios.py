"""Lógica de negocio de empresa. Las vistas llaman aquí; nunca calculan por su cuenta."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import Empresa, Seccion


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


def guardar_seccion(seccion: Seccion, usuario) -> Seccion:
    """Guarda una sección (alta o edición) con quién y cuándo."""
    if seccion._state.adding:
        seccion.creado_por = usuario
    seccion.modificado_por = usuario
    seccion.save()
    return seccion


def archivar_seccion(seccion: Seccion, usuario) -> Seccion:
    """Una sección no se borra: se archiva. Error claro si ya lo estaba."""
    if not seccion.activa:
        raise ValidationError(f"La sección {seccion.codigo} ya está archivada.")
    seccion.activa = False
    seccion.modificado_por = usuario
    seccion.save()
    return seccion


def reactivar_seccion(seccion: Seccion, usuario) -> Seccion:
    if seccion.activa:
        raise ValidationError(f"La sección {seccion.codigo} ya está activa.")
    seccion.activa = True
    seccion.modificado_por = usuario
    seccion.save()
    return seccion
