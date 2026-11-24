"""Selector de sección: la sección en la que trabaja cada usuario.

El usuario elige en la barra superior «Todas» o una sección activa; la elección se guarda en
`Usuario.seccion_elegida` y se recuerda al volver a entrar (se cambia con
core.servicios.elegir_seccion). Los componentes comunes la aplican solos:

- core/listas.preparar_lista: las listas de modelos con campo `seccion` se filtran por ella;
- core/formularios.FormularioBootstrap: en un alta, el campo `seccion` sale con ella por defecto
  (editable).

El selector **solo filtra**: se aplica sobre lo que la vista ya ha limitado por permisos y
ámbito, así que nunca da acceso a nada que el rol no vea.
"""

from django.core.exceptions import FieldDoesNotExist

CAMPO = "seccion"


def secciones_del_selector():
    """Secciones que se pueden elegir: las activas (las archivadas no salen)."""
    from empresa.models import Seccion

    return list(Seccion.objects.filter(activa=True).order_by("codigo"))


def seccion_elegida(usuario):
    """La sección elegida por el usuario, o None para «Todas». Una sección archivada cuenta
    como «Todas» (al archivarla se borra además de la elección de cada usuario)."""
    pk = getattr(usuario, "seccion_elegida_id", None)
    if not pk or not getattr(usuario, "is_authenticated", False):
        return None
    from empresa.models import Seccion

    return Seccion.objects.filter(pk=pk, activa=True).first()


def filtra_por_seccion(modelo) -> bool:
    """¿El modelo tiene un campo `seccion` que apunta a las secciones de la empresa?"""
    from empresa.models import Seccion

    try:
        campo = modelo._meta.get_field(CAMPO)
    except FieldDoesNotExist:
        return False
    return campo.is_relation and campo.related_model is Seccion


def filtrar_por_seccion(queryset, usuario):
    """(queryset, sección) con el queryset reducido a la sección elegida. Con «Todas», o si el
    modelo no tiene sección, el queryset no cambia y la sección es None."""
    if not filtra_por_seccion(queryset.model):
        return queryset, None
    seccion = seccion_elegida(usuario)
    if seccion is None:
        return queryset, None
    return queryset.filter(**{CAMPO: seccion}), seccion
