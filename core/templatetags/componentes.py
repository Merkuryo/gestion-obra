"""Etiquetas auxiliares de los componentes de templates/componentes."""

from django import template

from core.historial import historial as _historial
from core.permisos import puede_ver_historial

register = template.Library()


@register.simple_tag
def etapas_estado(etapas, actual):
    """Marca cada etapa como 'hecha', 'actual' o 'pendiente' según la etapa actual."""
    claves = [clave for clave, _ in etapas]
    posicion = claves.index(actual) if actual in claves else -1
    resultado = []
    for i, (clave, texto) in enumerate(etapas):
        if i == posicion:
            estado = "actual"
        elif posicion != -1 and i < posicion:
            estado = "hecha"
        else:
            estado = "pendiente"
        resultado.append({"clave": clave, "texto": texto, "estado": estado})
    return resultado


@register.inclusion_tag("componentes/_historial.html", takes_context=True)
def historial(context, objeto):
    """Historial al pie de una ficha: `{% historial objeto %}`. No pinta nada si el objeto
    aún no existe o el usuario no puede ver su parte."""
    usuario = context["request"].user
    if objeto is None or objeto.pk is None or not puede_ver_historial(usuario, objeto):
        return {"pagina": None}
    return {"pagina": _historial(objeto, usuario)}
