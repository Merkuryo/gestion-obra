import pytest

from core.models import Rol
from core.permisos import ACCIONES, PARTES


def datos_rol(nombre, usuarios=(), activo=True, **permisos):
    """POST del formulario del rol. `permisos`: parte=[acciones] (ámbito: todas)."""
    datos = {"nombre": nombre, "descripcion": "", "usuarios": [u.pk for u in usuarios]}
    if activo:
        datos["activo"] = "on"
    for parte in PARTES:
        datos[f"{parte}__ambito"] = "todas"
        for accion in permisos.get(parte, []):
            datos[f"{parte}__{accion}"] = "on"
    return datos


@pytest.mark.parametrize("rol", ["Administración", "Oficina técnica", "Jefe de obra", None])
def test_solo_quien_gestiona_roles_entra(cliente_con_rol, empresa, rol):
    cliente = cliente_con_rol(rol)
    assert cliente.get("/ajustes/roles/").status_code == 403
    assert cliente.get("/ajustes/roles/nuevo/").status_code == 403


def test_lista_de_roles(cliente_con_rol, empresa):
    html = cliente_con_rol("Gerencia").get("/ajustes/roles/").content.decode()
    for nombre in ["Gerencia", "Administración", "Oficina técnica", "Jefe de obra", "Encargado"]:
        assert nombre in html
    assert "De serie" in html


def test_enlace_en_el_menu_de_ajustes_solo_para_quien_gestiona(cliente_con_rol, empresa):
    assert (
        'href="/ajustes/roles/"'
        in cliente_con_rol("Gerencia").get("/ajustes/empresa/").content.decode()
    )


@pytest.mark.django_db
def test_menu_sin_roles_para_administracion(cliente_con_rol, empresa):
    html = cliente_con_rol("Administración").get("/ajustes/empresa/").content.decode()
    assert 'href="/ajustes/roles/"' not in html


def test_ficha_con_tabla_de_permisos(cliente_con_rol, empresa):
    rol = Rol.objects.get(nombre="Gerencia")
    html = cliente_con_rol("Gerencia").get(f"/ajustes/roles/{rol.pk}/").content.decode()
    for nombre in list(PARTES.values()) + list(ACCIONES.values()):
        assert nombre in html
    assert 'name="ajustes__editar"' in html and "Solo las asignadas" in html
    assert "Duplicar" in html


def test_crear_rol_con_permisos_y_usuarios(cliente_con_rol, empresa, django_user_model):
    cliente = cliente_con_rol("Gerencia")
    compras = django_user_model.objects.create_user("compras", password="x")
    respuesta = cliente.post(
        "/ajustes/roles/nuevo/", datos_rol("Compras", [compras], ajustes=["ver"])
    )
    assert respuesta.status_code == 302
    rol = Rol.objects.get(nombre="Compras")
    assert not rol.es_plantilla and rol.grupo.name == "Compras"
    assert list(rol.grupo.user_set.all()) == [compras]
    permiso = rol.permisos.get(parte="ajustes")
    assert permiso.ver and not permiso.editar


def test_el_nuevo_rol_cambia_lo_que_ve_el_usuario_sin_tocar_codigo(
    cliente_con_rol, empresa, django_user_model, client
):
    cliente_con_rol("Gerencia").post(
        "/ajustes/roles/nuevo/", datos_rol("Consulta", [], ajustes=["ver"])
    )
    usuario = django_user_model.objects.create_user("consulta", password="x")
    usuario.groups.add(Rol.objects.get(nombre="Consulta").grupo)
    client.force_login(usuario)
    assert client.get("/ajustes/empresa/").status_code == 200


def test_quitar_ajustes_a_oficina_tecnica_desde_la_pantalla(
    cliente_con_rol, empresa, usuario_con_rol, client
):
    tecnico = usuario_con_rol("Oficina técnica")
    rol = Rol.objects.get(nombre="Oficina técnica")
    gerencia = cliente_con_rol("Gerencia")
    respuesta = gerencia.post(f"/ajustes/roles/{rol.pk}/", datos_rol(rol.nombre, [tecnico]))
    assert respuesta.status_code == 302
    client.force_login(tecnico)
    assert client.get("/ajustes/empresa/").status_code == 403


def test_nombre_repetido(cliente_con_rol, empresa):
    respuesta = cliente_con_rol("Gerencia").post("/ajustes/roles/nuevo/", datos_rol("gerencia"))
    assert respuesta.status_code == 200
    assert "Ya hay un rol" in respuesta.content.decode()


def test_quitarse_la_gestion_a_si_mismo_muestra_el_error(cliente_con_rol, empresa):
    cliente = cliente_con_rol("Gerencia")
    rol = Rol.objects.get(nombre="Gerencia")
    respuesta = cliente.post(
        f"/ajustes/roles/{rol.pk}/", datos_rol("Gerencia", [], ajustes=["editar"])
    )
    assert respuesta.status_code == 200
    assert "al menos un rol" in respuesta.content.decode()
    assert rol.permisos.filter(parte="roles", editar=True).exists()
    assert rol.grupo.user_set.exists()


def test_duplicar_desde_la_ficha(cliente_con_rol, empresa):
    cliente = cliente_con_rol("Gerencia")
    rol = Rol.objects.get(nombre="Jefe de obra")
    respuesta = cliente.post(f"/ajustes/roles/{rol.pk}/duplicar/")
    copia = Rol.objects.get(nombre="Copia de Jefe de obra")
    assert respuesta.status_code == 302 and respuesta.url == f"/ajustes/roles/{copia.pk}/"
    assert copia.ver_margenes and not copia.ver_nominas


def test_duplicar_solo_por_post(cliente_con_rol, empresa):
    rol = Rol.objects.get(nombre="Gerencia")
    assert cliente_con_rol("Gerencia").get(f"/ajustes/roles/{rol.pk}/duplicar/").status_code == 405
