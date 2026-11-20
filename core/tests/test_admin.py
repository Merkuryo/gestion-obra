import pytest
from django.contrib.auth.models import Group


@pytest.fixture
def superusuario(client, django_user_model):
    root = django_user_model.objects.create_superuser("root", password="x")
    client.force_login(root)
    return root


@pytest.mark.django_db
def test_admin_de_usuarios_muestra_los_roles_sin_poder_cambiarlos(
    client, superusuario, usuario_con_rol
):
    tecnico = usuario_con_rol("Oficina técnica")
    html = client.get(f"/admin/core/usuario/{tecnico.pk}/change/").content.decode()
    assert html.count("Oficina técnica") >= 1
    assert 'name="groups"' not in html


@pytest.mark.django_db
def test_guardar_un_usuario_en_el_admin_no_cambia_sus_roles(client, superusuario, usuario_con_rol):
    tecnico = usuario_con_rol("Oficina técnica")
    gerencia = Group.objects.get(name="Gerencia")
    respuesta = client.post(
        f"/admin/core/usuario/{tecnico.pk}/change/",
        {
            "username": tecnico.username,
            "first_name": "Nuevo",
            "is_active": "on",
            "date_joined_0": "20/11/2026",
            "date_joined_1": "10:00:00",
            "groups": [gerencia.pk],
        },
    )
    assert respuesta.status_code == 302
    tecnico.refresh_from_db()
    assert tecnico.first_name == "Nuevo"  # el resto del usuario sí se guarda
    assert list(tecnico.groups.values_list("name", flat=True)) == ["Oficina técnica"]
