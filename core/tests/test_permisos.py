import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.views import View

from core.permisos import ConPermiso, nivel, puede_editar, puede_ver


def usuario_con_rol(django_user_model, rol):
    u = django_user_model.objects.create_user(f"u_{rol}".replace(" ", "_"), password="x")
    if rol:
        u.groups.add(Group.objects.get(name=rol))
    return u


@pytest.mark.django_db
@pytest.mark.parametrize(
    "rol, ver, editar",
    [
        ("Gerencia", True, True),
        ("Administración", True, False),
        ("Oficina técnica", True, False),
        ("Jefe de obra", False, False),
        (None, False, False),
    ],
)
def test_matriz_ajustes(django_user_model, rol, ver, editar):
    u = usuario_con_rol(django_user_model, rol)
    assert puede_ver(u, "ajustes") is ver
    assert puede_editar(u, "ajustes") is editar


@pytest.mark.django_db
def test_superusuario_todo(django_user_model):
    u = django_user_model.objects.create_superuser("root", password="x")
    assert nivel(u, "ajustes") == "E"
    assert nivel(u, "modulo_inexistente") == "E"


def test_anonimo_sin_acceso():
    assert nivel(AnonymousUser(), "ajustes") is None


@pytest.mark.django_db
def test_modulo_desconocido_sin_acceso(django_user_model):
    u = usuario_con_rol(django_user_model, "Gerencia")
    assert nivel(u, "modulo_inexistente") is None


@pytest.mark.django_db
def test_mixin_ver_vs_escribir(django_user_model):
    class Vista(ConPermiso, View):
        modulo = "ajustes"

        def get(self, request):
            return HttpResponse("ok")

        def post(self, request):
            return HttpResponse("guardado")

    rf = RequestFactory()
    admin = usuario_con_rol(django_user_model, "Administración")
    gerente = usuario_con_rol(django_user_model, "Gerencia")
    jefe = usuario_con_rol(django_user_model, "Jefe de obra")

    def llamar(metodo, usuario):
        peticion = getattr(rf, metodo)("/")
        peticion.user = usuario
        return Vista.as_view()(peticion)

    assert llamar("get", admin).status_code == 200
    assert llamar("get", gerente).status_code == 200
    assert llamar("post", gerente).status_code == 200
    with pytest.raises(PermissionDenied):
        llamar("post", admin)
    with pytest.raises(PermissionDenied):
        llamar("get", jefe)
    assert llamar("get", AnonymousUser()).status_code == 302
