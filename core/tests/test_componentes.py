import pytest
from django.contrib.auth.models import Group
from django.template.loader import render_to_string


def test_migas_tres_niveles_ultimo_sin_enlace():
    html = render_to_string(
        "componentes/_migas.html",
        {"migas": [("Ajustes", "/ajustes/"), ("Secciones", "/ajustes/secciones/"), ("PyL", "")]},
    )
    assert html.count("<li") == 3
    assert '<a href="/ajustes/">Ajustes</a>' in html
    assert '<a href="/ajustes/secciones/">Secciones</a>' in html
    assert 'aria-current="page">PyL</li>' in html
    assert '<a href="">' not in html


def test_migas_vacias_no_pintan_nada():
    assert render_to_string("componentes/_migas.html", {"migas": []}).strip() == ""


@pytest.mark.django_db
def test_filtros_de_permisos(django_user_model):
    from django.template import Context, Template

    u = django_user_model.objects.create_user("ana", password="x")
    u.groups.add(Group.objects.get(name="Administración"))
    plantilla = Template(
        '{% load permisos %}{{ u|puede_ver:"ajustes" }}-{{ u|puede_editar:"ajustes" }}'
    )
    assert plantilla.render(Context({"u": u})) == "True-False"
