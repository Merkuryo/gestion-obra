import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import include, path


def vista_prohibida(request):
    raise PermissionDenied


urlpatterns = [
    path("prohibido/", vista_prohibida),
    path("cuentas/", include("django.contrib.auth.urls")),
    path("", include("core.urls")),
]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="core.tests.test_403")
def test_403_extiende_de_base_y_vuelve_al_inicio(client, django_user_model):
    client.force_login(django_user_model.objects.create_user("ana", password="x"))
    r = client.get("/prohibido/")
    assert r.status_code == 403
    html = r.content.decode()
    assert "No tienes permiso" in html
    assert 'href="/"' in html and "Volver al inicio" in html
    # Viene de base.html: lleva la barra superior y los estilos de Bootstrap
    assert "navbar" in html and "bootstrap-5.3.8/bootstrap.min.css" in html
