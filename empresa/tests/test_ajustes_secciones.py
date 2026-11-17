from decimal import Decimal

import pytest
from django.urls import reverse

from empresa.models import Seccion

LISTA = "/ajustes/secciones/"
NUEVA = "/ajustes/secciones/nueva/"
FASES = [
    {"nombre": "Replanteo", "pct": 10},
    {"nombre": "Tendido", "pct": 40},
    {"nombre": "Conexionado", "pct": 30},
    {"nombre": "Pruebas", "pct": 20},
]


def datos(**cambios):
    base = {
        "codigo": "INS",
        "nombre": "Instalaciones",
        "pct_beneficio_defecto": "15.00",
        "activa": "on",
    }
    base.update(cambios)
    return base


@pytest.fixture
def instalaciones(db):
    return Seccion.objects.create(
        codigo="INS",
        nombre="Instalaciones",
        pct_beneficio_defecto=Decimal("15.00"),
        plantilla_fases=FASES,
    )


def url(pk, accion=""):
    return f"{LISTA}{pk}/{accion + '/' if accion else ''}"


# --- Acceso ------------------------------------------------------------------------------------
@pytest.mark.parametrize("ruta", ["lista", "nueva", "ficha", "archivar", "reactivar"])
def test_anonimo_va_al_login(client, instalaciones, ruta):
    rutas = {
        "lista": LISTA,
        "nueva": NUEVA,
        "ficha": url(instalaciones.pk),
        "archivar": url(instalaciones.pk, "archivar"),
        "reactivar": url(instalaciones.pk, "reactivar"),
    }
    for metodo in (client.get, client.post):
        r = metodo(rutas[ruta])
        assert r.status_code == 302 and "/cuentas/login/" in r.url


@pytest.mark.parametrize("rol", ["Jefe de obra", None])
def test_sin_acceso_recibe_403(cliente_con_rol, instalaciones, rol):
    c = cliente_con_rol(rol)
    for ruta in (LISTA, NUEVA, url(instalaciones.pk)):
        assert c.get(ruta).status_code == 403
    assert c.post(NUEVA, datos(codigo="XX")).status_code == 403
    assert c.post(url(instalaciones.pk, "archivar")).status_code == 403


@pytest.mark.parametrize("rol", ["Administración", "Oficina técnica"])
def test_administracion_y_ot_solo_ven(cliente_con_rol, instalaciones, rol):
    c = cliente_con_rol(rol)
    html = c.get(LISTA).content.decode()
    assert "Instalaciones" in html and "Nueva sección" not in html
    html = c.get(url(instalaciones.pk)).content.decode()
    assert "Solo lectura" in html and "Guardar" not in html and "Archivar" not in html
    assert c.get(NUEVA).status_code == 403
    assert c.post(NUEVA, datos(codigo="XX")).status_code == 403
    assert c.post(url(instalaciones.pk), datos(nombre="Cambiada")).status_code == 403
    assert c.post(url(instalaciones.pk, "archivar")).status_code == 403
    instalaciones.refresh_from_db()
    assert instalaciones.nombre == "Instalaciones" and instalaciones.activa
    assert not Seccion.objects.filter(codigo="XX").exists()


# --- Lista -------------------------------------------------------------------------------------
@pytest.fixture
def tres(instalaciones):
    Seccion.objects.create(
        codigo="ALB", nombre="Albañilería", pct_beneficio_defecto=Decimal("10.50")
    )
    Seccion.objects.create(
        codigo="CAR", nombre="Carpintería", pct_beneficio_defecto=Decimal("4.25"), activa=False
    )


def codigos(respuesta):
    return [f["celdas"][0]["valor"] for f in respuesta.context["lista"]["filas"]]


def test_lista_gerencia_con_boton_nueva_y_menu(cliente_con_rol, tres):
    r = cliente_con_rol("Gerencia").get(LISTA)
    html = r.content.decode()
    assert r.status_code == 200 and "Nueva sección" in html
    assert codigos(r) == ["ALB", "CAR", "INS"]  # orden por código del modelo
    assert "Activa" in html and "Archivada" in html
    assert 'href="/ajustes/empresa/"' in html and 'href="/ajustes/secciones/"' in html  # menú
    assert "breadcrumb" in html and "10,50" in html
    assert f'href="{url(Seccion.objects.get(codigo="INS").pk)}"' in html


def test_lista_busqueda_y_orden(cliente_con_rol, tres):
    c = cliente_con_rol("Gerencia")
    assert codigos(c.get(LISTA, {"q": "alb"})) == ["ALB"]
    assert codigos(c.get(LISTA, {"q": "zzz"})) == []
    assert codigos(c.get(LISTA, {"orden": "-codigo"})) == ["INS", "CAR", "ALB"]
    assert codigos(c.get(LISTA, {"orden": "pct_beneficio_defecto"})) == ["CAR", "ALB", "INS"]
    assert codigos(c.get(LISTA, {"orden": "campo_inventado"})) == ["ALB", "CAR", "INS"]


def test_lista_vacia(cliente_con_rol, db):
    assert "Todavía no hay registros" in cliente_con_rol("Gerencia").get(LISTA).content.decode()


def test_la_lista_no_usa_barra_de_estado(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    assert 'aria-label="Estado"' not in c.get(LISTA).content.decode()
    assert 'aria-label="Estado"' not in c.get(url(instalaciones.pk)).content.decode()


# --- Crear y editar ----------------------------------------------------------------------------
def test_crear_seccion(cliente_con_rol, db):
    r = cliente_con_rol("Gerencia").post(NUEVA, datos(codigo="PYL", nombre="Pladur"), follow=True)
    assert r.redirect_chain == [(LISTA, 302)]
    assert "Sección PYL guardada" in r.content.decode()
    s = Seccion.objects.get(codigo="PYL")
    assert s.nombre == "Pladur" and s.pct_beneficio_defecto == Decimal("15.00") and s.activa
    assert s.creado_por.username == "u_Gerencia" and s.history.count() == 1


def test_formulario_nuevo_sin_acciones_de_archivo(cliente_con_rol, db):
    html = cliente_con_rol("Gerencia").get(NUEVA).content.decode()
    assert "Guardar" in html and "Archivar" not in html
    assert "plantilla_fases" not in html and "nav-tabs" not in html


def test_editar_seccion_conserva_las_fases(cliente_con_rol, instalaciones):
    r = cliente_con_rol("Gerencia").post(
        url(instalaciones.pk),
        datos(nombre="Instalaciones y climatización", pct_beneficio_defecto="12.50"),
    )
    assert r.status_code == 302
    instalaciones.refresh_from_db()
    assert (
        instalaciones.nombre == "Instalaciones y climatización"
        and instalaciones.pct_beneficio_defecto == Decimal("12.50")
    )
    assert instalaciones.plantilla_fases == FASES
    assert (
        instalaciones.modificado_por.username == "u_Gerencia" and instalaciones.history.count() == 2
    )


def test_ficha_inexistente_da_404(cliente_con_rol, db):
    assert cliente_con_rol("Gerencia").get(url(999)).status_code == 404


# --- Validaciones del modelo -------------------------------------------------------------------
@pytest.mark.parametrize(
    "cambio, campo",
    [
        ({"codigo": "INSTA"}, "codigo"),  # más de 4 caracteres
        ({"codigo": "P-T"}, "codigo"),  # carácter no permitido
        ({"codigo": ""}, "codigo"),
        ({"codigo": "P T"}, "codigo"),
        ({"nombre": ""}, "nombre"),
        ({"pct_beneficio_defecto": "101"}, "pct_beneficio_defecto"),
        ({"pct_beneficio_defecto": "-1"}, "pct_beneficio_defecto"),
        ({"pct_beneficio_defecto": "abc"}, "pct_beneficio_defecto"),
    ],
)
def test_datos_no_validos_no_se_guardan(cliente_con_rol, db, cambio, campo):
    r = cliente_con_rol("Gerencia").post(NUEVA, datos(**cambio))
    assert r.status_code == 200 and campo in r.context["form"].errors
    assert "invalid-feedback" in r.content.decode()
    assert Seccion.objects.count() == 0


def test_codigo_en_minusculas_se_guarda_en_mayusculas(cliente_con_rol, db):
    cliente_con_rol("Gerencia").post(NUEVA, datos(codigo="pyl", nombre="Pladur"))
    assert list(Seccion.objects.values_list("codigo", flat=True)) == ["PYL"]


def test_codigo_repetido_tambien_en_minusculas(cliente_con_rol, instalaciones):
    r = cliente_con_rol("Gerencia").post(NUEVA, datos(codigo="ins", nombre="Otra"))
    assert r.status_code == 200 and "codigo" in r.context["form"].errors
    assert Seccion.objects.count() == 1


def test_codigo_no_se_puede_cambiar_al_editar(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    html = c.get(url(instalaciones.pk)).content.decode()
    assert "No se puede cambiar después" in html
    assert 'name="codigo"' in html and "disabled" in html
    r = c.post(url(instalaciones.pk), datos(codigo="XX", nombre="Instalaciones 2"))
    assert r.status_code == 302
    instalaciones.refresh_from_db()
    assert instalaciones.codigo == "INS" and instalaciones.nombre == "Instalaciones 2"
    assert not Seccion.objects.filter(codigo="XX").exists()


def test_codigo_es_de_solo_lectura_en_el_admin(client, django_user_model, instalaciones):
    client.force_login(django_user_model.objects.create_superuser("root", password="x"))
    html = client.get(f"/admin/empresa/seccion/{instalaciones.pk}/change/").content.decode()
    assert 'name="codigo"' not in html
    assert 'name="codigo"' in client.get("/admin/empresa/seccion/add/").content.decode()


def test_codigo_repetido(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    r = c.post(NUEVA, datos(nombre="Otras instalaciones"))
    assert r.status_code == 200 and "codigo" in r.context["form"].errors
    assert Seccion.objects.count() == 1
    # Editar una sección conservando su propio código sí vale
    assert c.post(url(instalaciones.pk), datos(nombre="Instalaciones 2")).status_code == 302


# --- Archivar y reactivar ----------------------------------------------------------------------
def test_archivar_no_borra(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    r = c.post(url(instalaciones.pk, "archivar"), follow=True)
    assert "Sección INS archivada" in r.content.decode()
    instalaciones.refresh_from_db()
    assert not instalaciones.activa and Seccion.objects.count() == 1
    assert (
        instalaciones.modificado_por.username == "u_Gerencia" and instalaciones.history.count() == 2
    )
    ficha = c.get(url(instalaciones.pk)).content.decode()
    assert "Archivada" in ficha and "Reactivar" in ficha
    assert f'formaction="{url(instalaciones.pk, "reactivar")}"' in ficha


def test_reactivar(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    c.post(url(instalaciones.pk, "archivar"))
    r = c.post(url(instalaciones.pk, "reactivar"), follow=True)
    assert "Sección INS reactivada" in r.content.decode()
    instalaciones.refresh_from_db()
    assert instalaciones.activa and instalaciones.history.count() == 3
    assert "Archivar" in c.get(url(instalaciones.pk)).content.decode()


def test_archivar_dos_veces_avisa_sin_error(cliente_con_rol, instalaciones):
    c = cliente_con_rol("Gerencia")
    c.post(url(instalaciones.pk, "archivar"))
    r = c.post(url(instalaciones.pk, "archivar"), follow=True)
    assert r.status_code == 200 and "ya está archivada" in r.content.decode()
    assert instalaciones.history.count() == 2
    c.post(url(instalaciones.pk, "reactivar"))
    r = c.post(url(instalaciones.pk, "reactivar"), follow=True)
    assert "ya está activa" in r.content.decode()


def test_archivar_solo_por_post(cliente_con_rol, instalaciones):
    assert cliente_con_rol("Gerencia").get(url(instalaciones.pk, "archivar")).status_code == 405
    instalaciones.refresh_from_db()
    assert instalaciones.activa


def test_archivar_seccion_inexistente(cliente_con_rol, db):
    assert cliente_con_rol("Gerencia").post(url(999, "archivar")).status_code == 404


def test_urls_inversas(instalaciones):
    assert reverse("empresa:secciones") == LISTA
    assert reverse("empresa:seccion_archivar", args=[instalaciones.pk]) == url(
        instalaciones.pk, "archivar"
    )
