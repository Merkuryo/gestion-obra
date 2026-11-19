"""Nombres de los roles de serie (grupos de Django).

Son las plantillas que crean las migraciones; cada empresa puede editarlas y crear otros
roles en Ajustes → Roles. Los permisos se consultan siempre en core/permisos.py.
"""

GERENCIA = "Gerencia"
ADMINISTRACION = "Administración"
OFICINA_TECNICA = "Oficina técnica"
JEFE_OBRA = "Jefe de obra"
ENCARGADO = "Encargado"

ROLES = [GERENCIA, ADMINISTRACION, OFICINA_TECNICA, JEFE_OBRA, ENCARGADO]


def tiene_rol(usuario, *roles: str) -> bool:
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    return usuario.groups.filter(name__in=roles).exists()
