"""Permisos por rol y parte de la app. Única capa que decide permisos.

Las vistas no comprueban roles por su cuenta: usan `ConPermiso` o estas funciones.
Los permisos de cada rol se guardan en la base de datos (core.models.Rol y PermisoRol) y se
editan en Ajustes → Roles. Lo que es fijo es la lista de PARTES: cada módulo nuevo añade aquí
su parte y, con una migración de datos, los permisos por defecto de las plantillas.

Niveles de `nivel()`: "E" crear/editar (incluye ver) · "V" solo ver · None = sin acceso.
"""

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

EDITAR = "E"
VER = "V"

# Partes de la app sobre las que se dan permisos (clave → nombre en pantalla)
PARTES = {
    "ajustes": "Empresa, secciones y ajustes",
    "roles": "Usuarios y roles",
}

ACCIONES = {
    "ver": "Ver",
    "crear": "Crear",
    "editar": "Editar",
    "aprobar": "Aprobar",
    "ver_importes": "Ver importes",
}

PARTE_ROLES = "roles"  # quien puede editarla gestiona los roles y los usuarios
_CACHE = "_permisos_por_rol"


def _permisos(usuario):
    """[(rol, {parte: PermisoRol})] de los roles activos del usuario. Se calcula una vez por
    objeto usuario, es decir, una vez por petición."""
    if not hasattr(usuario, _CACHE):
        from .models import Rol

        roles = Rol.objects.filter(activo=True, grupo__user=usuario).prefetch_related("permisos")
        setattr(usuario, _CACHE, [(r, {p.parte: p for p in r.permisos.all()}) for r in roles])
    return getattr(usuario, _CACHE)


def olvidar_permisos(usuario):
    """Descarta la caché del usuario (tras cambiar sus roles en la misma petición)."""
    if hasattr(usuario, _CACHE):
        delattr(usuario, _CACHE)


def _obra_permitida(permiso, usuario, obra):
    if permiso.ambito != permiso.ASIGNADAS or obra is None:
        return True
    # Las obras implementarán `esta_asignada(usuario)` cuando existan las asignaciones
    return bool(obra.esta_asignada(usuario))


def puede(usuario, parte: str, accion: str, obra=None) -> bool:
    """¿Puede `usuario` hacer `accion` en `parte`? Suma los permisos de todos sus roles.

    Con ámbito «solo asignadas», si se indica una obra, exige que esté asignada al usuario.
    Sin obra (p. ej. una lista) basta con tener el permiso; la lista filtrará por obra.
    """
    if accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {accion}")
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    for _rol, permisos in _permisos(usuario):
        permiso = permisos.get(parte)
        if permiso and getattr(permiso, accion) and _obra_permitida(permiso, usuario, obra):
            return True
    return False


def nivel(usuario, modulo: str) -> str | None:
    """Nivel más alto que el usuario tiene en el módulo (E, V o None)."""
    if puede(usuario, modulo, "crear") or puede(usuario, modulo, "editar"):
        return EDITAR
    return VER if puede(usuario, modulo, "ver") else None


def puede_ver(usuario, modulo: str) -> bool:
    return nivel(usuario, modulo) is not None


def puede_editar(usuario, modulo: str) -> bool:
    return nivel(usuario, modulo) == EDITAR


def gestiona_roles(usuario) -> bool:
    return puede(usuario, PARTE_ROLES, "editar")


def puede_ver_nominas(usuario) -> bool:
    """Nóminas individuales: permiso propio del rol, sin ámbito."""
    if not usuario.is_authenticated:
        return False
    return usuario.is_superuser or any(r.ver_nominas for r, _ in _permisos(usuario))


def puede_ver_margenes(usuario, parte: str, obra=None) -> bool:
    """Márgenes y beneficio de `parte`: hace falta, en un mismo rol, el permiso «ver márgenes»
    y poder ver esa parte (respetando su ámbito)."""
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    for rol, permisos in _permisos(usuario):
        permiso = permisos.get(parte)
        if rol.ver_margenes and permiso and permiso.ver and _obra_permitida(permiso, usuario, obra):
            return True
    return False


def obra_de(objeto):
    """Obra a la que pertenece `objeto` para aplicar el ámbito: la propia obra (si sabe decir
    a quién está asignada) o su atributo `obra`. None si no va ligado a ninguna."""
    if hasattr(objeto, "esta_asignada"):
        return objeto
    return getattr(objeto, "obra", None)


def puede_ver_historial(usuario, objeto) -> bool:
    """El historial de una ficha lo ve quien puede ver su parte (`parte_permisos` del modelo).
    El de los roles, como sus fichas, solo quien gestiona roles."""
    parte = getattr(objeto, "parte_permisos", None)
    if objeto is None or parte is None:
        return False
    if parte == PARTE_ROLES:
        return gestiona_roles(usuario)
    return puede(usuario, parte, "ver", obra=obra_de(objeto))


def campos_ocultos(usuario, objeto, modelo=None) -> set:
    """Campos de `modelo` (por defecto, el de `objeto`) que el usuario no puede ver en la ficha
    de `objeto`: los de importes sin «ver importes» en su parte y los de nóminas sin ese
    permiso."""
    modelo = modelo or type(objeto)
    ocultos = set()
    importes = getattr(modelo, "campos_importe", ())
    if importes and not puede(usuario, objeto.parte_permisos, "ver_importes", obra=obra_de(objeto)):
        ocultos.update(importes)
    nominas = getattr(modelo, "campos_nominas", ())
    if nominas and not puede_ver_nominas(usuario):
        ocultos.update(nominas)
    return ocultos


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


class ConGestionRoles(AccessMixin):
    """Mixin de las pantallas de roles: solo para quien gestiona roles, también para ver."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not gestiona_roles(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
