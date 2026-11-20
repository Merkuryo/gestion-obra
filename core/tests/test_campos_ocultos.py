"""Lo que no puedes ver, no lo puedes editar: listas, formularios e historial."""

from decimal import Decimal

import pytest
from django.test import RequestFactory

from core import servicios
from core.listas import Columna, preparar_lista
from core.models import Rol
from empresa.formularios import SeccionForm
from empresa.models import Seccion

PCT = "pct_beneficio_defecto"


@pytest.fixture
def sin_margenes(usuario_con_rol, django_user_model):
    """Edita Ajustes pero no tiene «ver márgenes»."""
    gerente = usuario_con_rol("Gerencia")
    rol = servicios.guardar_rol(
        Rol(nombre="Ajustes sin márgenes"), {"ajustes": {"editar": True}}, None, gerente
    )
    usuario = django_user_model.objects.create_user("sin_margenes", password="x")
    usuario.groups.add(rol.grupo)
    return usuario


@pytest.fixture
def seccion(db):
    return Seccion.objects.create(codigo="INS", nombre="Instalaciones", **{PCT: Decimal("27.50")})


def datos(**cambios):
    base = {"codigo": "INS", "nombre": "Instalaciones", PCT: "99.00", "activa": "on"}
    return {**base, **cambios}


# ── Lista ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_lista_oculta_la_columna_sin_ver_margenes(client, empresa, seccion, sin_margenes):
    client.force_login(sin_margenes)
    html = client.get("/ajustes/secciones/").content.decode()
    assert "(oculto)" in html and "27,50" not in html
    assert f"orden={PCT}" not in html  # ni se ordena por ella


@pytest.mark.django_db
def test_lista_con_ver_margenes(cliente_con_rol, empresa, seccion):
    html = cliente_con_rol("Gerencia").get("/ajustes/secciones/").content.decode()
    assert "27,50" in html and "(oculto)" not in html


@pytest.mark.django_db
def test_lista_sin_total_orden_ni_busqueda_por_lo_oculto(seccion, sin_margenes):
    Seccion.objects.create(codigo="ALB", nombre="Albañilería", **{PCT: Decimal("10.00")})
    columnas = [Columna("codigo", "Código"), Columna(PCT, "% beneficio", numerica=True, total=True)]

    def lista(query):
        request = RequestFactory().get("/" + query)
        request.user = sin_margenes
        return preparar_lista(request, Seccion.objects.order_by("codigo"), columnas, (PCT,))

    resultado = lista(f"?orden=-{PCT}")
    assert resultado["orden"] == "" and resultado["totales"] is None
    assert [f["celdas"][1]["valor"] for f in resultado["filas"]] == ["(oculto)", "(oculto)"]
    assert resultado["cabeceras"][1]["url_orden"] is None
    assert lista("?q=27")["num_registros"] == 2  # la búsqueda no mira el campo oculto


# ── Formulario ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_formulario_muestra_oculto_sin_el_valor(client, empresa, seccion, sin_margenes):
    client.force_login(sin_margenes)
    html = client.get(f"/ajustes/secciones/{seccion.pk}/").content.decode()
    assert 'value="(oculto)"' in html and "27.50" not in html and "27,50" not in html
    assert "Necesitas el permiso «ver márgenes» para verlo." in html


@pytest.mark.django_db
def test_al_guardar_se_conserva_el_valor_oculto(client, empresa, seccion, sin_margenes):
    client.force_login(sin_margenes)
    respuesta = client.post(f"/ajustes/secciones/{seccion.pk}/", datos(nombre="Fontanería"))
    assert respuesta.status_code == 302
    seccion.refresh_from_db()
    assert seccion.nombre == "Fontanería" and seccion.pct_beneficio_defecto == Decimal("27.50")


@pytest.mark.django_db
def test_alta_sin_ver_margenes_toma_el_valor_por_defecto(client, empresa, sin_margenes):
    client.force_login(sin_margenes)
    client.post("/ajustes/secciones/nueva/", datos(codigo="PT", nombre="Pintura"))
    assert Seccion.objects.get(codigo="PT").pct_beneficio_defecto == Decimal("15.00")


@pytest.mark.django_db
def test_con_ver_margenes_se_edita(cliente_con_rol, empresa, seccion):
    cliente = cliente_con_rol("Gerencia")
    cliente.post(f"/ajustes/secciones/{seccion.pk}/", datos())
    seccion.refresh_from_db()
    assert seccion.pct_beneficio_defecto == Decimal("99.00")


@pytest.mark.django_db
def test_formulario_sin_usuario_oculta_por_seguridad(seccion):
    form = SeccionForm(instance=seccion)
    assert form.ocultos == {PCT: "ver márgenes"} and form.fields[PCT].disabled
    assert 'value="(oculto)"' in str(form[PCT])
