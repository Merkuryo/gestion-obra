from types import SimpleNamespace

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory

from core import servicios
from core.formularios import FormularioBootstrap
from core.listas import Columna, preparar_lista
from core.secciones import filtrar_por_seccion, seccion_elegida
from core.tests.app_pruebas.models import DocumentoDePrueba
from empresa import servicios as servicios_empresa
from empresa.models import Seccion

URL = "/seccion/"
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def secciones(db):
    return {
        codigo: Seccion.objects.create(codigo=codigo, nombre=nombre)
        for codigo, nombre in [("PT", "Pintura"), ("PYL", "Pladur"), ("ALB", "Albañilería")]
    }


@pytest.fixture
def documentos(secciones):
    pt, pyl = secciones["PT"], secciones["PYL"]
    return [
        DocumentoDePrueba.objects.create(nombre="Fachada", seccion=pt, asignado=True),
        DocumentoDePrueba.objects.create(nombre="Interior", seccion=pt),
        DocumentoDePrueba.objects.create(nombre="Techos", seccion=pyl, asignado=True),
    ]


def _recargar(usuario):
    return type(usuario).objects.get(pk=usuario.pk)


# ── Persistencia por usuario ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_la_eleccion_se_guarda_por_usuario(usuario_con_rol, django_user_model, secciones):
    u = usuario_con_rol("Gerencia")
    otro = django_user_model.objects.create_user("otro", password="x")
    servicios.elegir_seccion(u, secciones["PT"])

    assert seccion_elegida(_recargar(u)) == secciones["PT"]  # en la base de datos
    assert seccion_elegida(_recargar(otro)) is None  # cada usuario la suya

    servicios.elegir_seccion(u, None)
    assert seccion_elegida(_recargar(u)) is None


def test_se_recuerda_al_volver_a_entrar(client, usuario_con_rol, empresa, secciones):
    u = usuario_con_rol("Gerencia")
    client.force_login(u)
    client.post(URL, {"seccion": secciones["PYL"].pk}, **HTMX)
    client.logout()

    client.force_login(_recargar(u))
    html = client.get("/").content.decode()
    assert "PYL · Pladur" in html


@pytest.mark.django_db
def test_no_se_elige_una_seccion_archivada(usuario_con_rol, secciones):
    alb = secciones["ALB"]
    servicios_empresa.archivar_seccion(alb, None)
    with pytest.raises(ValidationError, match="archivada"):
        servicios.elegir_seccion(usuario_con_rol("Gerencia"), alb)


# ── Selector en la barra y cambio con HTMX ─────────────────────────────────────


def test_selector_en_la_barra_con_las_activas(cliente_con_rol, empresa, secciones):
    servicios_empresa.archivar_seccion(secciones["ALB"], None)
    html = cliente_con_rol("Encargado").get("/").content.decode()
    assert 'id="selector-seccion"' in html and "Todas las secciones" in html
    assert "PT — Pintura" in html and "PYL — Pladur" in html
    assert "Albañilería" not in html  # archivada: no sale


def test_sin_secciones_no_hay_selector(cliente_con_rol, empresa):
    assert 'id="selector-seccion"' not in cliente_con_rol("Gerencia").get("/").content.decode()


def test_cambio_con_htmx_devuelve_el_selector_sin_salir_de_la_pagina(
    client, usuario_con_rol, empresa, secciones
):
    u = usuario_con_rol("Gerencia")
    client.force_login(u)
    respuesta = client.post(
        URL, {"seccion": secciones["PT"].pk, "siguiente": "/ajustes/secciones/?q=a"}, **HTMX
    )
    assert respuesta.status_code == 200
    assert respuesta["HX-Trigger"] == "seccion-cambiada"
    html = respuesta.content.decode()
    assert html.lstrip().startswith("<form") and "<html" not in html  # solo el selector
    assert "PT · Pintura" in html
    assert 'value="/ajustes/secciones/?q=a"' in html  # sigue apuntando a la misma página
    assert _recargar(u).seccion_elegida == secciones["PT"]

    respuesta = client.post(URL, {"seccion": ""}, **HTMX)
    assert "Todas las secciones" in respuesta.content.decode()
    assert _recargar(u).seccion_elegida is None


def test_sin_javascript_vuelve_a_la_misma_pagina(client, usuario_con_rol, empresa, secciones):
    client.force_login(usuario_con_rol("Gerencia"))
    datos = {"seccion": secciones["PT"].pk, "siguiente": "/ajustes/secciones/"}
    respuesta = client.post(URL, datos)
    assert respuesta.status_code == 302 and respuesta.url == "/ajustes/secciones/"
    # Nunca redirige fuera de la app
    respuesta = client.post(URL, {"seccion": "", "siguiente": "https://ejemplo.invalid/"})
    assert respuesta.url == "/"


@pytest.mark.parametrize("valor", ["abc", "999999"])
def test_seccion_no_valida(client, usuario_con_rol, empresa, secciones, valor):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    client.force_login(u)
    assert client.post(URL, {"seccion": valor}, **HTMX).status_code == 400
    assert _recargar(u).seccion_elegida == secciones["PT"]  # no cambia


def test_seccion_archivada_por_post_da_error(client, usuario_con_rol, empresa, secciones):
    servicios_empresa.archivar_seccion(secciones["ALB"], None)
    client.force_login(usuario_con_rol("Gerencia"))
    assert client.post(URL, {"seccion": secciones["ALB"].pk}, **HTMX).status_code == 400


def test_solo_post_y_con_sesion(client, usuario_con_rol, empresa, secciones):
    assert client.post(URL, {"seccion": ""}).status_code == 302  # a la pantalla de acceso
    client.force_login(usuario_con_rol("Gerencia"))
    assert client.get(URL).status_code == 405


# ── Sección archivada ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_al_archivar_la_seccion_vuelve_a_todas(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    servicios_empresa.archivar_seccion(secciones["PT"], u)
    assert _recargar(u).seccion_elegida is None
    servicios_empresa.reactivar_seccion(secciones["PT"], u)
    assert _recargar(u).seccion_elegida is None  # reactivarla no la vuelve a elegir


@pytest.mark.django_db
def test_archivar_desde_el_formulario_tambien_vuelve_a_todas(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    pt = secciones["PT"]
    pt.activa = False
    servicios_empresa.guardar_seccion(pt, u)
    assert _recargar(u).seccion_elegida is None


@pytest.mark.django_db
def test_una_seccion_archivada_por_otra_via_cuenta_como_todas(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    Seccion.objects.filter(pk=secciones["PT"].pk).update(activa=False)
    assert seccion_elegida(_recargar(u)) is None


def test_la_barra_muestra_todas_si_la_elegida_se_archivo(client, usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    Seccion.objects.filter(pk=secciones["PT"].pk).update(activa=False)
    client.force_login(u)
    assert "Todas las secciones" in client.get("/").content.decode()


# ── Filtro común de las listas ─────────────────────────────────────────────────

COLUMNAS = [Columna("nombre", "Nombre"), Columna("seccion", "Sección", ordenable=False)]


def _lista(usuario, queryset=None, **opciones):
    request = RequestFactory().get("/documentos/")
    request.user = usuario
    queryset = DocumentoDePrueba.objects.all() if queryset is None else queryset
    return preparar_lista(request, queryset, COLUMNAS, buscar_en=("nombre",), **opciones)


def _nombres(lista):
    return [f["objeto"].nombre for f in lista["filas"]]


@pytest.mark.django_db
def test_la_lista_se_filtra_sola_por_la_seccion_elegida(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    lista = _lista(u)
    assert _nombres(lista) == ["Fachada", "Interior", "Techos"]  # «Todas»: sin filtro
    assert lista["filtra_seccion"] and lista["seccion"] is None

    servicios.elegir_seccion(u, secciones["PT"])
    lista = _lista(u)
    assert _nombres(lista) == ["Fachada", "Interior"]
    assert lista["seccion"] == secciones["PT"] and lista["num_registros"] == 2


@pytest.mark.django_db
def test_el_filtro_se_combina_con_la_busqueda(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    request = RequestFactory().get("/documentos/", {"q": "tech"})
    request.user = u
    lista = preparar_lista(request, DocumentoDePrueba.objects.all(), COLUMNAS, ("nombre",))
    assert _nombres(lista) == []  # «Techos» es de Pladur


@pytest.mark.django_db
def test_lista_sin_filtro_de_seccion_si_se_desactiva(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    lista = _lista(u, por_seccion=False)
    assert len(lista["filas"]) == 3 and not lista["filtra_seccion"]


@pytest.mark.django_db
def test_modelos_sin_seccion_no_se_filtran(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    queryset, seccion = filtrar_por_seccion(Seccion.objects.all(), u)
    assert queryset.count() == 3 and seccion is None


def test_indicador_en_la_cabecera_de_la_lista(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    request = RequestFactory().get("/documentos/")
    request.user = u
    lista = preparar_lista(request, DocumentoDePrueba.objects.all(), COLUMNAS)
    html = render_to_string("componentes/_lista.html", {"lista": lista}, request=request)
    assert "Mostrando: <strong>Pintura</strong>" in html
    assert "ver todas" in html and 'name="seccion" value=""' in html
    assert "<div data-filtra-seccion>" in html  # se recarga al cambiar el selector

    servicios.elegir_seccion(u, None)
    lista = preparar_lista(request, DocumentoDePrueba.objects.all(), COLUMNAS)
    html = render_to_string("componentes/_lista.html", {"lista": lista}, request=request)
    assert "Mostrando:" not in html and "<div data-filtra-seccion>" in html


def test_lista_sin_seccion_sin_indicador(cliente_con_rol, empresa, secciones):
    html = cliente_con_rol("Gerencia").get("/ajustes/secciones/").content.decode()
    assert "<div data-filtra-seccion>" not in html and "Mostrando:" not in html


# ── El selector filtra; nunca da acceso ────────────────────────────────────────


@pytest.mark.django_db
def test_el_filtro_solo_reduce_lo_que_la_vista_ya_limito(usuario_con_rol, documentos, secciones):
    """La vista limita por ámbito (aquí, solo los asignados) y el selector reduce sobre eso:
    elegir la sección de un documento no asignado no lo hace aparecer."""
    u = usuario_con_rol("Jefe de obra")
    asignados = DocumentoDePrueba.objects.filter(asignado=True)
    servicios.elegir_seccion(u, secciones["PT"])
    assert _nombres(_lista(u, asignados)) == ["Fachada"]  # no «Interior», que no está asignado
    servicios.elegir_seccion(u, secciones["PYL"])
    assert _nombres(_lista(u, asignados)) == ["Techos"]
    servicios.elegir_seccion(u, None)
    assert _nombres(_lista(u, asignados)) == ["Fachada", "Techos"]


def test_elegir_seccion_no_da_acceso_a_pantallas_sin_permiso(client, usuario_con_rol, secciones):
    encargado = usuario_con_rol("Encargado")  # sin acceso a Ajustes
    client.force_login(encargado)
    assert client.post(URL, {"seccion": secciones["PT"].pk}, **HTMX).status_code == 200
    assert client.get("/ajustes/secciones/").status_code == 403
    assert client.get(f"/ajustes/secciones/{secciones['PT'].pk}/").status_code == 403


@pytest.mark.django_db
def test_elegir_seccion_no_cambia_los_permisos(usuario_con_rol, secciones):
    from core.permisos import nivel, puede_ver_margenes

    u = usuario_con_rol("Encargado")
    antes = (nivel(u, "ajustes"), puede_ver_margenes(u, "ajustes"))
    servicios.elegir_seccion(u, secciones["PT"])
    u = _recargar(u)
    assert (nivel(u, "ajustes"), puede_ver_margenes(u, "ajustes")) == antes == (None, False)


# ── Valor por defecto en las altas ─────────────────────────────────────────────


class DocumentoForm(FormularioBootstrap, forms.ModelForm):
    class Meta:
        model = DocumentoDePrueba
        fields = ["nombre", "seccion"]


@pytest.mark.django_db
def test_alta_con_la_seccion_elegida_por_defecto_y_editable(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    assert DocumentoForm(usuario=u)["seccion"].value() is None  # «Todas»: sin valor

    servicios.elegir_seccion(u, secciones["PT"])
    form = DocumentoForm(usuario=u)
    assert form["seccion"].value() == secciones["PT"].pk
    assert not form.fields["seccion"].disabled
    assert f'value="{secciones["PT"].pk}" selected' in str(form["seccion"])

    # Se puede cambiar al guardar
    form = DocumentoForm({"nombre": "Patio", "seccion": secciones["PYL"].pk}, usuario=u)
    assert form.is_valid()
    assert form.save().seccion == secciones["PYL"]


@pytest.mark.django_db
def test_al_editar_no_se_cambia_la_seccion(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PYL"])
    fachada = documentos[0]  # de Pintura
    assert DocumentoForm(instance=fachada, usuario=u)["seccion"].value() == secciones["PT"].pk


@pytest.mark.django_db
def test_la_vista_puede_traer_otra_seccion(usuario_con_rol, secciones):
    u = usuario_con_rol("Gerencia")
    servicios.elegir_seccion(u, secciones["PT"])
    form = DocumentoForm(usuario=u, initial={"seccion": secciones["PYL"].pk})
    assert form["seccion"].value() == secciones["PYL"].pk


@pytest.mark.django_db
def test_sin_usuario_no_hay_seccion_por_defecto(secciones):
    assert DocumentoForm()["seccion"].value() is None


def test_selector_con_usuario_anonimo():
    assert seccion_elegida(SimpleNamespace(is_authenticated=False, seccion_elegida_id=1)) is None


# ── Secciones que ofrece el campo ──────────────────────────────────────────────


def _opciones(form):
    return {s.pk for s in form.fields["seccion"].queryset}


@pytest.mark.django_db
def test_en_un_alta_solo_se_ofrecen_las_activas(usuario_con_rol, secciones):
    servicios_empresa.archivar_seccion(secciones["ALB"], None)
    form = DocumentoForm(usuario=usuario_con_rol("Gerencia"))
    assert _opciones(form) == {secciones["PT"].pk, secciones["PYL"].pk}
    assert "Albañilería" not in str(form["seccion"])

    # Tampoco se acepta por POST
    form = DocumentoForm({"nombre": "Muro", "seccion": secciones["ALB"].pk})
    assert not form.is_valid() and "seccion" in form.errors


@pytest.mark.django_db
def test_al_editar_se_conserva_la_seccion_archivada(usuario_con_rol, documentos, secciones):
    u = usuario_con_rol("Gerencia")
    techos = documentos[2]  # de Pladur
    servicios_empresa.archivar_seccion(secciones["PYL"], u)

    form = DocumentoForm(instance=techos, usuario=u)
    assert _opciones(form) == {s.pk for s in secciones.values()}  # las activas y la suya
    assert form["seccion"].value() == secciones["PYL"].pk
    opcion = f'value="{secciones["PYL"].pk}" selected>PYL — Pladur (archivada)'
    assert opcion in str(form["seccion"])

    # Guardar sin tocar la sección la conserva
    form = DocumentoForm(
        {"nombre": "Techos altos", "seccion": secciones["PYL"].pk}, instance=techos, usuario=u
    )
    assert form.is_valid(), form.errors
    form.save()
    techos.refresh_from_db()
    assert techos.seccion == secciones["PYL"] and techos.nombre == "Techos altos"

    # Otra sección archivada distinta de la suya no se ofrece
    servicios_empresa.archivar_seccion(secciones["ALB"], u)
    form = DocumentoForm({"nombre": "Techos", "seccion": secciones["ALB"].pk}, instance=techos)
    assert not form.is_valid() and "seccion" in form.errors
