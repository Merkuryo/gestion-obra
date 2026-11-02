import pytest

from core.views import MODULOS


def test_gerencia_ve_ajustes_en_el_lanzador(cliente_con_rol, empresa):
    html = cliente_con_rol("Gerencia").get("/").content.decode()
    assert 'href="/ajustes/"' in html and "Ajustes" in html


@pytest.mark.parametrize("rol", ["Administración", "Oficina técnica"])
def test_administracion_y_ot_ven_ajustes(cliente_con_rol, empresa, rol):
    assert 'href="/ajustes/"' in cliente_con_rol(rol).get("/").content.decode()


@pytest.mark.parametrize("rol", ["Jefe de obra", None])
def test_jefe_de_obra_no_ve_ajustes(cliente_con_rol, empresa, rol):
    html = cliente_con_rol(rol).get("/").content.decode()
    assert 'href="/ajustes/"' not in html
    assert "Presupuestos" in html  # el resto del lanzador sigue


def test_el_lanzador_no_enlaza_ya_al_admin_de_django(cliente_con_rol, empresa):
    assert 'href="/admin/"' not in cliente_con_rol("Gerencia").get("/").content.decode()


@pytest.mark.django_db
def test_aviso_sin_empresa_enlaza_al_alta_en_ajustes(cliente_con_rol):
    html = cliente_con_rol("Gerencia").get("/").content.decode()
    assert "no está configurada" in html
    assert 'href="/ajustes/empresa/"' in html and "/admin/" not in html


@pytest.mark.django_db
def test_aviso_sin_empresa_para_quien_no_puede_ver_ajustes(cliente_con_rol):
    html = cliente_con_rol("Jefe de obra").get("/").content.decode()
    assert "no está configurada" in html and "pide a Gerencia" in html
    assert 'href="/ajustes/empresa/"' not in html


def test_menu_de_usuario_segun_rol(cliente_con_rol, empresa):
    html = cliente_con_rol("Administración").get("/").content.decode()
    assert 'href="/ajustes/"' in html and "Admin de Django" not in html
    assert 'href="/admin/"' not in html


def test_superusuario_ve_ajustes_y_admin_de_django(client, django_user_model, empresa):
    client.force_login(django_user_model.objects.create_superuser("root", password="x"))
    html = client.get("/").content.decode()
    assert 'href="/ajustes/"' in html and "Admin de Django" in html


def test_ajustes_es_el_unico_modulo_con_permiso_por_ahora():
    con_permiso = [m[0] for m in MODULOS if m[4]]
    assert con_permiso == ["Ajustes"]
