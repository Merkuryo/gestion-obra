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


# --- Formulario y barra de estado --------------------------------------------------------------
from django import forms  # noqa: E402

from core.formularios import FormularioBootstrap  # noqa: E402

ETAPAS = [("borrador", "Borrador"), ("enviado", "Enviado"), ("aceptado", "Aceptado")]


class FormularioPrueba(FormularioBootstrap, forms.Form):
    grupos = [("Datos", ["nombre", "activo"]), ("Económico", ["importe"])]
    nombre = forms.CharField(max_length=10)
    activo = forms.BooleanField(required=False)
    importe = forms.DecimalField(min_value=0, required=False)


class FormularioSinGrupos(FormularioBootstrap, forms.Form):
    nombre = forms.CharField()


def pintar(form, **extra):
    return render_to_string("componentes/_formulario.html", {"form": form, **extra})


def test_formulario_pone_clases_de_bootstrap():
    f = FormularioPrueba()
    assert "form-control" in f.fields["nombre"].widget.attrs["class"]
    assert "form-check-input" in f.fields["activo"].widget.attrs["class"]


def test_formulario_con_pestanas_y_barra_de_acciones():
    html = pintar(FormularioPrueba(), url_descartar="/volver/")
    assert "Guardar" in html and 'href="/volver/"' in html
    assert html.count('data-bs-toggle="tab"') == 2
    assert html.index("Guardar") < html.index("nav-tabs")  # acciones arriba


def test_formulario_sin_grupos_no_pinta_pestanas():
    html = pintar(FormularioSinGrupos())
    assert "nav-tabs" not in html and "Guardar" in html


def test_error_de_campo_marca_pestana_y_abre_la_que_falla():
    f = FormularioPrueba(data={"nombre": "ok", "importe": "-5"})
    assert not f.is_valid()
    grupos = f.grupos_renderizados()
    assert [g["con_errores"] for g in grupos] == [False, True]
    assert [g["activo"] for g in grupos] == [False, True]
    html = pintar(f)
    assert "invalid-feedback" in html and "bi-exclamation-circle-fill" in html


def test_solo_lectura_sin_guardar_y_campos_desactivados():
    html = pintar(FormularioPrueba(solo_lectura=True), url_descartar="/x/")
    assert "Guardar" not in html and "Descartar" not in html
    assert html.count("disabled") >= 3
    assert "Solo lectura" in html


def test_acciones_extra_enlace_y_post():
    acciones = [
        {"texto": "Ver", "url": "/ver/", "icono": "bi-eye"},
        {"texto": "Archivar", "url": "/archivar/", "post": True, "clase": "btn-outline-danger"},
    ]
    html = pintar(FormularioPrueba(), acciones=acciones)
    assert '<a href="/ver/"' in html
    assert 'formaction="/archivar/"' in html and "btn-outline-danger" in html


def test_barra_de_estado_marca_la_etapa_actual():
    html = render_to_string(
        "componentes/_barra_estado.html", {"etapas": ETAPAS, "actual": "enviado"}
    )
    assert html.count("btn-primary") == 1
    assert html.count("btn-secondary") == 1  # borrador, ya hecha
    assert html.count("btn-outline-secondary") == 1  # aceptado, pendiente
    assert 'aria-current="step">Enviado' in html


def test_barra_de_estado_con_etapa_desconocida_deja_todo_pendiente():
    html = render_to_string("componentes/_barra_estado.html", {"etapas": ETAPAS, "actual": "x"})
    assert html.count("btn-outline-secondary") == 3 and "btn-primary" not in html
