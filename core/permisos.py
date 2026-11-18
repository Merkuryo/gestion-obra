"""Permisos por rol y módulo. Única capa que decide permisos.

Las vistas no comprueban roles por su cuenta: usan `ConPermiso` o estas funciones.
Cada módulo nuevo añade a MATRIZ sus filas.
Niveles: "E" crear/editar (incluye ver) · "V" solo ver · ausente = sin acceso.
"""

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from .roles import ADMINISTRACION, GERENCIA, OFICINA_TECNICA

EDITAR = "E"
VER = "V"

MATRIZ = {
    # Ajustes: datos de la empresa, secciones y series de numeración
    "ajustes": {GERENCIA: EDITAR, ADMINISTRACION: VER, OFICINA_TECNICA: VER},
}


def nivel(usuario, modulo: str) -> str | None:
    """Nivel más alto que el usuario tiene en el módulo (E, V o None)."""
    if not usuario.is_authenticated:
        return None
    if usuario.is_superuser:
        return EDITAR
    roles = set(usuario.groups.values_list("name", flat=True))
    niveles = {n for rol, n in MATRIZ.get(modulo, {}).items() if rol in roles}
    if EDITAR in niveles:
        return EDITAR
    return VER if VER in niveles else None


def puede_ver(usuario, modulo: str) -> bool:
    return nivel(usuario, modulo) is not None


def puede_editar(usuario, modulo: str) -> bool:
    return nivel(usuario, modulo) == EDITAR


class ConPermiso(AccessMixin):
    """Mixin de vistas. `modulo` es obligatorio; `metodos_escritura` exigen nivel E.

    Sin login → pantalla de acceso. Con login pero sin permiso → 403.
    """

    modulo: str
    metodos_escritura = ("POST", "PUT", "PATCH", "DELETE")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        necesita_editar = request.method in self.metodos_escritura
        permitido = (
            puede_editar(request.user, self.modulo)
            if necesita_editar
            else puede_ver(request.user, self.modulo)
        )
        if not permitido:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
