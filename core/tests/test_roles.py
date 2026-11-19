from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from core import servicios
from core.models import PermisoRol, Rol
from core.permisos import (
    PARTES,
    gestiona_roles,
    nivel,
    olvidar_permisos,
    puede,
    puede_ver_margenes,
    puede_ver_nominas,
)

# Matriz fija de permisos anterior a los roles editables, tal como estaba en core/permisos.py
MATRIZ_ANTERIOR = {
    "ajustes": {"Gerencia": "E", "Administración": "V", "Oficina técnica": "V"},
}
ROLES_DE_SERIE = ["Gerencia", "Administración", "Oficina técnica", "Jefe de obra", "Encargado"]


def obra(*asignados):
    """Obra de prueba: las obras reales implementarán `esta_asignada(usuario)`."""
    return SimpleNamespace(esta_asignada=lambda u: u in asignados)


def rol_con(nombre, actor, permisos, **datos):
    return servicios.guardar_rol(Rol(nombre=nombre, **datos), permisos, None, actor)


@pytest.fixture
def gerente(usuario_con_rol):
    return usuario_con_rol("Gerencia")


# ── Migración ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize("rol", ROLES_DE_SERIE)
def test_la_matriz_migrada_da_lo_mismo_que_la_anterior(usuario_con_rol, rol):
    u = usuario_con_rol(rol)
    for modulo, niveles in MATRIZ_ANTERIOR.items():
        assert nivel(u, modulo) == niveles.get(rol), (rol, modulo)


@pytest.mark.django_db
def test_plantillas_de_serie():
    roles = {r.nombre: r for r in Rol.objects.all()}
    assert set(ROLES_DE_SERIE) <= set(roles)
    assert all(roles[n].es_plantilla for n in ROLES_DE_SERIE)
    assert all(roles[n].grupo.name == n for n in ROLES_DE_SERIE)
    assert {n for n in ROLES_DE_SERIE if roles[n].ver_nominas} == {"Gerencia", "Administración"}
    assert {n for n in ROLES_DE_SERIE if roles[n].ver_margenes} == {
        "Gerencia",
        "Administración",
        "Oficina técnica",
        "Jefe de obra",
    }
    assert not roles["Encargado"].permisos.exists()
    gestores = Rol.objects.filter(permisos__parte="roles", permisos__editar=True)
    assert list(gestores.values_list("nombre", flat=True)) == ["Gerencia"]


@pytest.mark.django_db
def test_partes_conocidas():
    assert set(PermisoRol.objects.values_list("parte", flat=True)) <= set(PARTES)


# ── Acciones, ámbito y roles inactivos ─────────────────────────────────────────


@pytest.mark.django_db
def test_cualquier_accion_incluye_ver(gerente):
    rol = rol_con("Revisor", gerente, {"ajustes": {"aprobar": True}})
    assert PermisoRol.objects.get(rol=rol, parte="ajustes").ver


@pytest.mark.django_db
def test_ambito_solo_asignadas(gerente, django_user_model):
    rol = rol_con("Técnico de zona", gerente, {"ajustes": {"ver": True, "ambito": "asignadas"}})
    u = django_user_model.objects.create_user("tecnico", password="x")
    u.groups.add(rol.grupo)
    otro = django_user_model.objects.create_user("otro", password="x")

    assert puede(u, "ajustes", "ver", obra=obra(u))
    assert not puede(u, "ajustes", "ver", obra=obra(otro))
    assert puede(u, "ajustes", "ver")  # sin obra (una lista): la lista filtrará
    assert not puede(u, "ajustes", "editar", obra=obra(u))


@pytest.mark.django_db
def test_un_rol_con_todas_las_obras_suma_sobre_otro_con_asignadas(gerente, django_user_model):
    asignadas = rol_con("Zona", gerente, {"ajustes": {"ver": True, "ambito": "asignadas"}})
    todas = rol_con("Consulta", gerente, {"ajustes": {"ver": True, "ambito": "todas"}})
    u = django_user_model.objects.create_user("mixto", password="x")
    u.groups.add(asignadas.grupo, todas.grupo)
    assert puede(u, "ajustes", "ver", obra=obra())


@pytest.mark.django_db
def test_rol_inactivo_no_da_permisos(usuario_con_rol):
    u = usuario_con_rol("Administración")
    Rol.objects.filter(nombre="Administración").update(activo=False)
    assert nivel(u, "ajustes") is None
    assert not puede_ver_nominas(u)


@pytest.mark.django_db
def test_accion_desconocida(gerente):
    with pytest.raises(ValueError):
        puede(gerente, "ajustes", "borrar")


# ── Nóminas y márgenes ─────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rol, ve", [("Gerencia", True), ("Administración", True), ("Oficina técnica", False),
                ("Jefe de obra", False), ("Encargado", False), (None, False)],
)  # fmt: skip
def test_permiso_de_nominas(usuario_con_rol, rol, ve):
    assert puede_ver_nominas(usuario_con_rol(rol)) is ve


@pytest.mark.django_db
def test_superusuario_ve_nominas_y_margenes(django_user_model):
    root = django_user_model.objects.create_superuser("root", password="x")
    assert puede_ver_nominas(root) and puede_ver_margenes(root, "ajustes", obra=obra())


@pytest.mark.django_db
def test_ver_margenes_respeta_la_parte_y_el_ambito(gerente, django_user_model):
    jefe = rol_con(
        "Jefe de zona",
        gerente,
        {"ajustes": {"ver": True, "ambito": "asignadas"}},
        ver_margenes=True,
    )
    sin_margenes = rol_con("Consulta sin márgenes", gerente, {"ajustes": {"ver": True}})
    u = django_user_model.objects.create_user("jz", password="x")
    u.groups.add(jefe.grupo)
    v = django_user_model.objects.create_user("cs", password="x")
    v.groups.add(sin_margenes.grupo)

    assert puede_ver_margenes(u, "ajustes", obra=obra(u))
    assert not puede_ver_margenes(u, "ajustes", obra=obra())  # obra no asignada
    assert not puede_ver_margenes(u, "roles")  # no ve la parte
    assert not puede_ver_margenes(v, "ajustes")  # ve la parte, sin permiso de márgenes


@pytest.mark.django_db
def test_encargado_no_ve_margenes(usuario_con_rol):
    assert not puede_ver_margenes(usuario_con_rol("Encargado"), "ajustes")


# ── Salvaguardas ───────────────────────────────────────────────────────────────


def _rol(nombre):
    return Rol.objects.get(nombre=nombre)


@pytest.mark.django_db
def test_no_se_puede_quitar_la_gestion_al_ultimo_rol_que_la_tiene(django_user_model):
    root = django_user_model.objects.create_superuser("root", password="x")
    gerencia = _rol("Gerencia")
    with pytest.raises(ValidationError, match="al menos un rol"):
        servicios.guardar_rol(gerencia, {"ajustes": {"editar": True}}, None, root)
    assert gestiona_roles_de(_rol("Gerencia"))  # no se guardó nada


@pytest.mark.django_db
def test_no_se_puede_desactivar_el_ultimo_rol_que_gestiona(django_user_model):
    root = django_user_model.objects.create_superuser("root", password="x")
    gerencia = _rol("Gerencia")
    gerencia.activo = False
    with pytest.raises(ValidationError, match="al menos un rol"):
        servicios.guardar_rol(gerencia, servicios.permisos_de(gerencia), None, root)
    assert _rol("Gerencia").activo


@pytest.mark.django_db
def test_nadie_se_quita_a_si_mismo_la_gestion(gerente):
    rol_con("Gestores", gerente, {"roles": {"editar": True}})  # queda otro rol que gestiona
    gerencia = _rol("Gerencia")
    with pytest.raises(ValidationError, match="a ti mismo"):
        servicios.guardar_rol(gerencia, servicios.permisos_de(gerencia), set(), gerente)
    assert gerente in _rol("Gerencia").grupo.user_set.all()

    with pytest.raises(ValidationError, match="a ti mismo"):
        servicios.guardar_rol(gerencia, {"ajustes": {"editar": True}}, None, gerente)


@pytest.mark.django_db
def test_si_otro_rol_suyo_gestiona_si_puede(gerente):
    gestores = rol_con("Gestores", gerente, {"roles": {"editar": True}})
    gerente.groups.add(gestores.grupo)
    olvidar_permisos(gerente)
    gerencia = _rol("Gerencia")
    servicios.guardar_rol(gerencia, {"ajustes": {"editar": True}}, None, gerente)
    assert not gestiona_roles_de(_rol("Gerencia"))
    olvidar_permisos(gerente)
    assert gestiona_roles(gerente)


@pytest.mark.django_db
def test_plantilla_no_se_renombra(gerente):
    gerencia = _rol("Gerencia")
    gerencia.nombre = "Dirección"
    with pytest.raises(ValidationError):
        servicios.guardar_rol(gerencia, servicios.permisos_de(gerencia), None, gerente)
    assert Group.objects.filter(name="Gerencia").exists()


def gestiona_roles_de(rol):
    return rol.permisos.filter(parte="roles", editar=True).exists()


# ── Duplicar e historial ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_duplicar_plantilla(gerente):
    copia = servicios.duplicar_rol(_rol("Gerencia"), gerente)
    otra = servicios.duplicar_rol(_rol("Gerencia"), gerente)
    assert copia.nombre == "Copia de Gerencia" and otra.nombre == "Copia de Gerencia (2)"
    assert not copia.es_plantilla and copia.ver_nominas and copia.ver_margenes
    assert servicios.permisos_de(copia) == servicios.permisos_de(_rol("Gerencia"))
    assert not copia.grupo.user_set.exists()


@pytest.mark.django_db
def test_historial_de_cambios_y_usuarios(gerente, django_user_model):
    rol = rol_con("Compras", gerente, {"ajustes": {"ver": True}})
    nuevo = django_user_model.objects.create_user("compras1", password="x")
    servicios.guardar_rol(rol, {"ajustes": {"editar": True}}, {nuevo}, gerente)

    ultimo = rol.history.first()
    assert ultimo.history_change_reason == "Usuarios: entran compras1"
    assert rol.history.count() == 2
    permiso = PermisoRol.objects.get(rol=rol, parte="ajustes")
    assert permiso.history.count() == 2 and permiso.editar
    assert rol.modificado_por == gerente
