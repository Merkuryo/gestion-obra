from decimal import Decimal

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from core.listas import Columna, preparar_lista
from empresa.models import Seccion

COLUMNAS = [
    Columna("codigo", "Código"),
    Columna("nombre", "Nombre"),
    Columna("pct_beneficio_defecto", "% beneficio", numerica=True, total=True),
    Columna(
        "activa",
        "Estado",
        ordenable=False,
        tipo="badge",
        valor=lambda s: ("Activa", "success") if s.activa else ("Archivada", "secondary"),
    ),
]


@pytest.fixture
def secciones(db):
    for codigo, nombre, pct, activa in [
        ("INS", "Instalaciones", "15.00", True),
        ("ALB", "Albañilería", "10.50", True),
        ("CAR", "Carpintería", "4.25", False),
    ]:
        Seccion.objects.create(
            codigo=codigo, nombre=nombre, pct_beneficio_defecto=Decimal(pct), activa=activa
        )


def lista(query="", **kw):
    request = RequestFactory().get("/ajustes/secciones/" + query)
    return preparar_lista(
        request, Seccion.objects.all(), COLUMNAS, buscar_en=("codigo", "nombre"), **kw
    )


def codigos(resultado):
    return [f["celdas"][0]["valor"] for f in resultado["filas"]]


def test_orden_ascendente_y_descendente(secciones):
    assert codigos(lista("?orden=codigo")) == ["ALB", "CAR", "INS"]
    assert codigos(lista("?orden=-codigo")) == ["INS", "CAR", "ALB"]
    assert codigos(lista("?orden=pct_beneficio_defecto")) == ["CAR", "ALB", "INS"]


def test_orden_por_campo_no_permitido_se_ignora(secciones):
    # "activa" no es ordenable y "creado_por" no es columna: se mantiene el orden del queryset
    assert codigos(lista("?orden=activa")) == codigos(lista())
    assert codigos(lista("?orden=creado_por")) == codigos(lista())
    assert lista("?orden=activa")["orden"] == ""


def test_cabecera_alterna_el_sentido(secciones):
    cab = lista("?orden=codigo")["cabeceras"][0]
    assert cab["flecha"] == "asc" and "orden=-codigo" in cab["url_orden"]
    cab = lista("?orden=-codigo")["cabeceras"][0]
    assert cab["flecha"] == "desc" and "orden=codigo" in cab["url_orden"]
    assert lista()["cabeceras"][3]["url_orden"] is None  # no ordenable


def test_busqueda_por_codigo_o_nombre(secciones):
    assert codigos(lista("?q=inst")) == ["INS"]
    assert codigos(lista("?q=ALB")) == ["ALB"]
    assert codigos(lista("?q=zzz")) == []


def test_busqueda_exige_todas_las_palabras(secciones):
    assert codigos(lista("?q=ins instalaciones")) == ["INS"]
    assert codigos(lista("?q=ins carp")) == []


def test_totales_decimal_exacto_y_recuento(secciones):
    r = lista()
    assert r["num_registros"] == 3
    assert r["totales"][2]["valor"] == Decimal("29.75")
    assert r["totales"][0] is None  # columnas sin total


def test_totales_siguen_a_la_busqueda(secciones):
    r = lista("?q=al")  # INS y ALB; "Carpintería" no contiene "al"
    assert r["num_registros"] == 2
    assert r["totales"][2]["valor"] == Decimal("25.50")


def test_total_cero_si_no_hay_filas(db):
    assert lista()["totales"][2]["valor"] == Decimal("0")


def test_url_de_fila(secciones):
    r = lista(url_fila=lambda s: f"/ajustes/secciones/{s.pk}/")
    assert all(f["url"].startswith("/ajustes/secciones/") for f in r["filas"])


def test_plantilla_pinta_cifras_badges_y_totales(secciones):
    html = render_to_string("componentes/_lista.html", {"lista": lista("?orden=codigo")})
    assert "15,00" in html and "29,75" in html  # formato español
    assert 'class="cifra"' in html
    assert "text-bg-success" in html and "text-bg-secondary" in html
    assert "bi-caret-up-fill" in html
    assert "3 registros" in html


def test_plantilla_sin_resultados(secciones):
    html = render_to_string("componentes/_lista.html", {"lista": lista("?q=zzz")})
    assert "Ningún resultado para «zzz»" in html and "0 registros" in html
