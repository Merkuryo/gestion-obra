"""Etiquetas auxiliares de los componentes de templates/componentes."""

from django import template

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
