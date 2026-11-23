import re

import pytest
from django import forms

from core.formularios import FormularioBootstrap, RolForm, SelectorMultiple
from core.models import Rol
from core.tests.test_pantalla_roles import datos_rol

OPCIONES = [("1", "Árbol"), ("2", "Bancada"), ("3", "Cubierta")]


class FormularioDePrueba(FormularioBootstrap, forms.Form):
    elegidos = forms.MultipleChoiceField(
        choices=OPCIONES, required=False, widget=SelectorMultiple, label="Elementos"
    )


def test_pinta_casillas_busqueda_y_etiquetas():
    html = str(FormularioDePrueba(initial={"elegidos": ["2"]})["elegidos"])
    assert 'x-data="selectorMultiple"' in html
    assert html.count('type="checkbox"') == 3
    assert re.search(r'value="2" id="id_elegidos_1" checked', html)
    assert 'value="1" id="id_elegidos_0">' in html  # sin marcar
    assert 'type="search"' in html and "x-cloak" in html  # búsqueda solo con JavaScript
    assert 'aria-controls="id_elegidos_opciones"' in html
    # Cada casilla con su etiqueta: se maneja con el teclado sin el script
    for i, (_, texto) in enumerate(OPCIONES):
        assert f'<label class="form-check-label" for="id_elegidos_{i}">{texto}</label>' in html


def test_envia_los_valores_marcados():
    form = FormularioDePrueba({"elegidos": ["1", "3"]})
    assert form.is_valid() and form.cleaned_data["elegidos"] == ["1", "3"]


def test_desactivado_no_se_puede_cambiar():
    html = str(FormularioDePrueba(solo_lectura=True)["elegidos"])
    assert html.count("disabled") >= 3


def test_campo_agrupado_con_su_titulo():
    from django.template.loader import render_to_string

    form = FormularioDePrueba()
    html = render_to_string("componentes/_campo.html", {"campo": form["elegidos"]})
    assert 'role="group" aria-labelledby="id_elegidos_etiqueta"' in html
    assert 'id="id_elegidos_etiqueta">Elementos<' in html


def test_script_cargado_antes_de_alpine(cliente_con_rol, empresa):
    html = cliente_con_rol("Gerencia").get("/").content.decode()
    assert html.index("componentes/selector-multiple.js") < html.index("alpine.min.js")


# ── En los usuarios de un rol ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_los_usuarios_del_rol_usan_el_selector(usuario_con_rol):
    gerente = usuario_con_rol("Gerencia")
    form = RolForm(instance=Rol.objects.get(nombre="Gerencia"), usuario=gerente)
    assert isinstance(form.fields["usuarios"].widget, SelectorMultiple)
    html = str(form["usuarios"])
    assert 'x-data="selectorMultiple"' in html
    assert re.search(rf'value="{gerente.pk}" id="id_usuarios_\d+" checked', html)


def test_la_ficha_del_rol_guarda_los_usuarios_elegidos(cliente_con_rol, empresa, django_user_model):
    cliente = cliente_con_rol("Gerencia")
    a = django_user_model.objects.create_user("compras_a", password="x")
    b = django_user_model.objects.create_user("compras_b", password="x")
    html = cliente.get("/ajustes/roles/nuevo/").content.decode()
    assert 'x-data="selectorMultiple"' in html and "compras_a" in html

    cliente.post("/ajustes/roles/nuevo/", datos_rol("Compras", [a, b], ajustes=["ver"]))
    rol = Rol.objects.get(nombre="Compras")
    assert set(rol.grupo.user_set.all()) == {a, b}
    cliente.post(f"/ajustes/roles/{rol.pk}/", datos_rol("Compras", [b], ajustes=["ver"]))
    assert list(rol.grupo.user_set.all()) == [b]
