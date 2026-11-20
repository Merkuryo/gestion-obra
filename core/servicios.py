"""Lógica de roles y permisos. Las vistas llaman aquí; nunca cambian roles por su cuenta.

Salvaguardas (se comprueban al final de cada cambio, dentro de la transacción; si fallan,
no se guarda nada):
- siempre queda al menos un rol activo que gestiona los roles;
- quien hace el cambio no puede quitarse a sí mismo la gestión de roles.
"""

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import PermisoRol, Rol
from .permisos import ACCIONES, PARTE_ROLES, PARTES, gestiona_roles, olvidar_permisos


def _comprobar_salvaguardas(actor):
    con_gestion = Rol.objects.filter(
        activo=True, permisos__parte=PARTE_ROLES, permisos__editar=True
    )
    if not con_gestion.exists():
        raise ValidationError("Debe quedar al menos un rol activo que gestione los roles.")
    olvidar_permisos(actor)
    if not gestiona_roles(actor):
        raise ValidationError(
            "No puedes quitarte a ti mismo la gestión de roles. Pide a otra persona con ese "
            "permiso que haga el cambio."
        )


def _registrar(objeto, actor, motivo=None):
    if objeto._state.adding:
        objeto.creado_por = actor
    objeto.modificado_por = actor
    if motivo:
        objeto._change_reason = motivo
    objeto.save()


def _guardar_permisos(rol, permisos, actor):
    """`permisos`: {parte: {accion: bool, ..., "ambito": str}}. Partes no incluidas: sin acceso."""
    for parte in PARTES:
        datos = permisos.get(parte, {})
        acciones = {a: bool(datos.get(a)) for a in ACCIONES}
        existente = PermisoRol.objects.filter(rol=rol, parte=parte).first()
        if not any(acciones.values()):
            if existente:
                existente.delete()  # sin ninguna acción, la fila sobra (queda en el historial)
            continue
        permiso = existente or PermisoRol(rol=rol, parte=parte)
        for accion, valor in acciones.items():
            setattr(permiso, accion, valor)
        permiso.ambito = datos.get("ambito") or PermisoRol.TODAS
        _registrar(permiso, actor)


def _resumen_usuarios(antes, despues):
    entran = sorted(u.get_username() for u in despues - antes)
    salen = sorted(u.get_username() for u in antes - despues)
    partes = []
    if entran:
        partes.append("entran " + ", ".join(entran))
    if salen:
        partes.append("salen " + ", ".join(salen))
    return "Usuarios: " + "; ".join(partes) if partes else None


@transaction.atomic
def guardar_rol(rol, permisos, usuarios, actor):
    """Alta o edición de un rol: datos (ya puestos en `rol`), permisos por parte y usuarios.

    `usuarios`: conjunto de usuarios que deben tener el rol, o None para no tocarlos.
    """
    if rol.es_plantilla and rol.pk:
        original = Rol.objects.get(pk=rol.pk).nombre
        if rol.nombre != original:
            raise ValidationError({"nombre": "Los roles de serie no se pueden renombrar."})
    if rol.pk is None:
        rol.grupo = Group.objects.create(name=rol.nombre)
    elif rol.grupo.name != rol.nombre:
        rol.grupo.name = rol.nombre
        rol.grupo.save()

    motivo = None
    if usuarios is not None:
        antes = set(rol.grupo.user_set.all())
        rol.grupo.user_set.set(usuarios)
        motivo = _resumen_usuarios(antes, set(usuarios))
    _registrar(rol, actor, motivo)
    _guardar_permisos(rol, permisos, actor)
    _comprobar_salvaguardas(actor)
    return rol


def permisos_de(rol):
    """Permisos de un rol en el formato de `guardar_rol`."""
    return {
        p.parte: {**{a: getattr(p, a) for a in ACCIONES}, "ambito": p.ambito}
        for p in rol.permisos.all()
    }


def _nombre_libre(base):
    nombre, n = base, 2
    while Rol.objects.filter(nombre=nombre).exists() or Group.objects.filter(name=nombre).exists():
        nombre, n = f"{base} ({n})", n + 1
    return nombre


@transaction.atomic
def duplicar_rol(origen, actor):
    """Copia un rol (p. ej. una plantilla) con sus permisos, sin usuarios. Devuelve la copia."""
    copia = Rol(
        nombre=_nombre_libre(f"Copia de {origen.nombre}"),
        descripcion=origen.descripcion,
        ver_nominas=origen.ver_nominas,
        ver_margenes=origen.ver_margenes,
    )
    return guardar_rol(copia, permisos_de(origen), usuarios=None, actor=actor)
