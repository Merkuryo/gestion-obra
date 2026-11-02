from django.urls import reverse

from empresa.context_processors import tema_desde_color


def test_tema_por_defecto():
    t = tema_desde_color(None)
    assert t["primario"] == "#1F6FEB"
    assert t["primario_rgb"] == "31, 111, 235"


def test_tema_oscurece_para_hover():
    assert tema_desde_color("#FF0000")["primario_oscuro"] == "#d80000"


def test_inicio_usa_bootstrap_y_color_de_empresa(client, django_user_model, empresa):
    empresa.color_primario = "#C2410C"
    empresa.save()
    client.force_login(django_user_model.objects.create_user("ana", password="x"))
    html = client.get(reverse("inicio")).content.decode()
    assert "bootstrap-5.3.8/bootstrap.min.css" in html
    assert "--bs-primary: #C2410C" in html
    assert "Presupuestos" in html


def test_todas_las_paginas_extienden_de_base():
    """Una sola plantilla base: las páginas la extienden; las parciales (_*.html) no."""
    from pathlib import Path

    for plantilla in Path("templates").rglob("*.html"):
        if plantilla.name == "base.html" or plantilla.name.startswith("_"):
            continue
        assert '{% extends "base.html" %}' in plantilla.read_text(encoding="utf-8"), plantilla
