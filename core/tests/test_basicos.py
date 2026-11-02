import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from core.roles import ROLES, tiene_rol


@pytest.mark.django_db
def test_roles_creados_por_migracion():
    assert set(Group.objects.values_list("name", flat=True)) >= set(ROLES)


@pytest.mark.django_db
def test_tiene_rol(django_user_model):
    u = django_user_model.objects.create_user("ana", password="x")
    assert not tiene_rol(u, "Gerencia")
    u.groups.add(Group.objects.get(name="Gerencia"))
    assert tiene_rol(u, "Gerencia")


@pytest.mark.django_db
def test_inicio_pide_login(client):
    r = client.get(reverse("inicio"))
    assert r.status_code == 302 and "/cuentas/login/" in r.url


@pytest.mark.django_db
def test_inicio_avisa_si_no_hay_empresa(client, django_user_model):
    client.force_login(django_user_model.objects.create_user("ana", password="x"))
    r = client.get(reverse("inicio"))
    assert "no está configurada" in r.content.decode()
