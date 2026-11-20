"""Filtros para plantillas. Delegan en core/permisos.py; no deciden nada por su cuenta."""

from django import template

from core import permisos

register = template.Library()


@register.filter
def puede_ver(usuario, modulo):
    return permisos.puede_ver(usuario, modulo)


@register.filter
def puede_editar(usuario, modulo):
    return permisos.puede_editar(usuario, modulo)


@register.filter
def gestiona_roles(usuario):
    return permisos.gestiona_roles(usuario)


@register.filter
def puede_ver_nominas(usuario):
    return permisos.puede_ver_nominas(usuario)


@register.filter
def puede_ver_margenes(usuario, parte):
    return permisos.puede_ver_margenes(usuario, parte)
