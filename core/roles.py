"""Roles de usuario (grupos de Django).

Los permisos de cada rol por módulo están en core/permisos.py.
"""

GERENCIA = "Gerencia"
ADMINISTRACION = "Administración"
OFICINA_TECNICA = "Oficina técnica"
JEFE_OBRA = "Jefe de obra"

ROLES = [GERENCIA, ADMINISTRACION, OFICINA_TECNICA, JEFE_OBRA]


def tiene_rol(usuario, *roles: str) -> bool:
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    return usuario.groups.filter(name__in=roles).exists()
