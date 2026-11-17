import io
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from empresa.models import Empresa

URL = "/ajustes/empresa/"


def datos(**cambios):
    base = {
        "razon_social": "Construcciones Ejemplo SL",
        "nombre_comercial": "Demo",
        "cif": "B12345674",
        "color_primario": "#1F6FEB",
        "ancho_cliente": "3",
        "ancho_obra": "4",
        "pct_desvio_defecto": "2.00",
        "pct_desvio_tope": "4.00",
        "jornada_horas": "8.00",
        "recargo_festivos": "50.00",
        "dias_aviso_pago": "60",
        "umbral_aviso_coste_hora": "15.00",
    }
    base.update(cambios)
    return base


def imagen(nombre="logo.png", formato="PNG", tamano=(10, 10)):
    buffer = io.BytesIO()
    Image.new("RGB", tamano, "red").save(buffer, formato)
    return SimpleUploadedFile(nombre, buffer.getvalue())


@pytest.fixture(autouse=True)
def media_temporal(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


# --- Acceso ------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_anonimo_va_al_login(client, empresa):
    for metodo in (client.get, client.post):
        r = metodo(URL)
        assert r.status_code == 302 and "/cuentas/login/" in r.url


@pytest.mark.django_db
def test_ajustes_redirige_a_la_ficha(cliente_con_rol):
    r = cliente_con_rol("Gerencia").get("/ajustes/")
    assert r.status_code == 302 and r.url == URL


def test_jefe_de_obra_recibe_403_con_la_pagina_amable(cliente_con_rol, empresa):
    c = cliente_con_rol("Jefe de obra")
    assert c.get(URL).status_code == 403
    r = c.post(URL, datos())
    assert r.status_code == 403 and "No tienes permiso" in r.content.decode()


def test_usuario_sin_rol_recibe_403(cliente_con_rol, empresa):
    assert cliente_con_rol(None).get(URL).status_code == 403


@pytest.mark.parametrize("rol", ["Administración", "Oficina técnica"])
def test_administracion_y_ot_ven_en_solo_lectura(cliente_con_rol, empresa, rol):
    r = cliente_con_rol(rol).get(URL)
    html = r.content.decode()
    assert r.status_code == 200
    assert "Construcciones Ejemplo SL" in html and "Solo lectura" in html
    assert "Guardar" not in html and "disabled" in html


def test_administracion_no_puede_guardar(cliente_con_rol, empresa):
    r = cliente_con_rol("Administración").post(URL, datos(razon_social="Hackeada SL"))
    assert r.status_code == 403
    empresa.refresh_from_db()
    assert empresa.razon_social == "Construcciones Ejemplo SL"


def test_gerencia_ve_el_formulario_con_pestanas(cliente_con_rol, empresa):
    html = cliente_con_rol("Gerencia").get(URL).content.decode()
    assert "Guardar" in html and "Solo lectura" not in html
    for pestana in ("Identificación", "Imagen y documentos", "Parámetros económicos"):
        assert pestana in html
    assert 'type="color"' in html and 'enctype="multipart/form-data"' in html


def test_superusuario_puede_editar(client, django_user_model, empresa):
    client.force_login(django_user_model.objects.create_superuser("root", password="x"))
    r = client.post(URL, datos(telefono="930000000"))
    assert r.status_code == 302
    empresa.refresh_from_db()
    assert empresa.telefono == "930000000"


# --- Guardado ----------------------------------------------------------------------------------
def test_gerencia_guarda_y_queda_historial(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    r = c.post(
        URL, datos(razon_social="Construcciones Renovadas SL", telefono="930000000"), follow=True
    )
    assert r.redirect_chain == [(URL, 302)]
    assert "Datos de la empresa guardados" in r.content.decode()
    empresa.refresh_from_db()
    assert empresa.razon_social == "Construcciones Renovadas SL" and empresa.telefono == "930000000"
    assert empresa.modificado_por.username == "u_Gerencia"
    assert empresa.history.count() == 2


def test_cambiar_el_color_cambia_el_tema(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(color_primario="#C2410C"))
    html = c.get(URL).content.decode()
    assert "--bs-primary: #C2410C" in html


def test_cif_se_normaliza(cliente_con_rol, empresa):
    cliente_con_rol("Gerencia").post(URL, datos(cif="b-1234567-4"))
    empresa.refresh_from_db()
    assert empresa.cif == "B12345674"


@pytest.mark.django_db
def test_alta_cuando_no_hay_empresa(cliente_con_rol):
    c = cliente_con_rol("Gerencia")
    html = c.get(URL).content.decode()
    assert "Guardar" in html and "solo se eligen ahora" in html
    r = c.post(URL, datos(ancho_cliente="4", ancho_obra="5"))
    assert r.status_code == 302
    e = Empresa.actual()
    assert e.pk == Empresa.PK_UNICA and e.creado_por.username == "u_Gerencia"
    assert (e.ancho_cliente, e.ancho_obra) == (4, 5)  # en el alta sí se eligen


@pytest.mark.django_db
def test_sin_empresa_administracion_ve_aviso_sin_formulario(cliente_con_rol):
    html = cliente_con_rol("Administración").get(URL).content.decode()
    assert "aún no está configurada" in html and "Guardar" not in html


def test_los_anchos_no_cambian_despues_del_alta(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    assert c.post(URL, datos(ancho_cliente="5", ancho_obra="6")).status_code == 302
    empresa.refresh_from_db()
    assert (empresa.ancho_cliente, empresa.ancho_obra) == (3, 4)


# --- Validaciones del modelo -------------------------------------------------------------------
@pytest.mark.parametrize(
    "cambio, campo",
    [
        ({"cif": "B12345675"}, "cif"),
        ({"color_primario": "azul"}, "color_primario"),
        ({"pct_desvio_defecto": "5.00"}, "pct_desvio_defecto"),  # supera el tope del 4 %
        ({"pct_desvio_tope": "101"}, "pct_desvio_tope"),
        ({"email": "no-es-correo"}, "email"),
    ],
)
def test_datos_no_validos_no_se_guardan(cliente_con_rol, empresa, cambio, campo):
    r = cliente_con_rol("Gerencia").post(URL, datos(razon_social="Cambiada SL", **cambio))
    assert r.status_code == 200
    assert campo in r.context["form"].errors
    assert "invalid-feedback" in r.content.decode()
    empresa.refresh_from_db()
    assert empresa.razon_social == "Construcciones Ejemplo SL"


# --- Logo: solo PNG/JPG, máximo 2 MB, sin SVG ----------------------------------------------------
@pytest.mark.parametrize(
    "nombre, formato", [("logo.png", "PNG"), ("logo.jpg", "JPEG"), ("LOGO.JPEG", "JPEG")]
)
def test_logo_png_y_jpg_se_aceptan(cliente_con_rol, empresa, nombre, formato):
    c = cliente_con_rol("Gerencia")
    r = c.post(URL, datos(logo=imagen(nombre, formato)))
    assert r.status_code == 302
    empresa.refresh_from_db()
    assert empresa.logo.name.startswith("empresa/")
    assert os.path.exists(empresa.logo.path)
    assert empresa.logo.url in c.get(URL).content.decode()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>1</script></svg>'


def test_logo_svg_se_rechaza(cliente_con_rol, empresa):
    r = cliente_con_rol("Gerencia").post(
        URL, datos(logo=SimpleUploadedFile("logo.svg", SVG, "image/svg+xml"))
    )
    assert r.status_code == 200 and "logo" in r.context["form"].errors
    empresa.refresh_from_db()
    assert not empresa.logo


def test_svg_disfrazado_de_png_se_rechaza(cliente_con_rol, empresa):
    r = cliente_con_rol("Gerencia").post(
        URL, datos(logo=SimpleUploadedFile("logo.png", SVG, "image/png"))
    )
    assert r.status_code == 200 and "logo" in r.context["form"].errors


@pytest.mark.parametrize("nombre, formato", [("logo.gif", "GIF"), ("logo.webp", "WEBP")])
def test_logo_de_otro_formato_se_rechaza(cliente_con_rol, empresa, nombre, formato):
    r = cliente_con_rol("Gerencia").post(URL, datos(logo=imagen(nombre, formato)))
    assert r.status_code == 200 and "logo" in r.context["form"].errors


def test_imagen_png_con_extension_gif_se_rechaza(cliente_con_rol, empresa):
    r = cliente_con_rol("Gerencia").post(URL, datos(logo=imagen("logo.gif", "PNG")))
    assert r.status_code == 200 and "PNG o JPG" in str(r.context["form"].errors["logo"])


def test_logo_de_mas_de_2_mb_se_rechaza(cliente_con_rol, empresa):
    ruido = Image.frombytes("RGB", (1000, 1000), os.urandom(3_000_000))
    buffer = io.BytesIO()
    ruido.save(buffer, "PNG")
    assert len(buffer.getvalue()) > 2 * 1024 * 1024
    r = cliente_con_rol("Gerencia").post(
        URL, datos(logo=SimpleUploadedFile("logo.png", buffer.getvalue()))
    )
    assert r.status_code == 200
    assert "2 MB" in str(r.context["form"].errors["logo"])
    empresa.refresh_from_db()
    assert not empresa.logo


def test_guardar_sin_subir_logo_conserva_el_anterior(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(logo=imagen()))
    anterior = Empresa.actual().logo.name
    c.post(URL, datos(telefono="930000000"))
    assert Empresa.actual().logo.name == anterior


def test_url_inversa():
    assert reverse("empresa:ficha") == URL


# --- Quitar el logo ------------------------------------------------------------------------------
def test_sin_logo_no_se_ofrece_quitarlo(cliente_con_rol, empresa):
    html = cliente_con_rol("Gerencia").get(URL).content.decode()
    assert "Quitar el logo actual" not in html


def test_con_logo_se_ofrece_quitarlo(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(logo=imagen()))
    assert "Quitar el logo actual" in c.get(URL).content.decode()


def test_quitar_logo_borra_el_archivo_y_la_referencia(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(logo=imagen()))
    ruta = Empresa.actual().logo.path
    assert os.path.exists(ruta)
    r = c.post(URL, datos(quitar_logo="on"), follow=True)
    assert r.status_code == 200 and "Datos de la empresa guardados" in r.content.decode()
    assert not Empresa.actual().logo and not os.path.exists(ruta)
    assert "Sin logo" in r.content.decode() and "Quitar el logo actual" not in r.content.decode()
    assert Empresa.actual().history.count() == 3  # alta del logo + quitarlo


def test_quitar_logo_no_toca_otros_datos(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(logo=imagen(), telefono="930000000"))
    c.post(URL, datos(telefono="930000000", quitar_logo="on"))
    e = Empresa.actual()
    assert e.telefono == "930000000" and e.razon_social == "Construcciones Ejemplo SL"


def test_subir_y_quitar_a_la_vez_es_un_error(cliente_con_rol, empresa):
    c = cliente_con_rol("Gerencia")
    c.post(URL, datos(logo=imagen()))
    anterior = Empresa.actual().logo.name
    r = c.post(URL, datos(logo=imagen("otro.png"), quitar_logo="on"))
    assert r.status_code == 200 and "quitar_logo" in r.context["form"].errors
    assert Empresa.actual().logo.name == anterior and os.path.exists(Empresa.actual().logo.path)


def test_administracion_no_puede_quitar_el_logo(client, usuario_con_rol, empresa):
    empresa.logo.save("logo.png", imagen(), save=True)
    client.force_login(usuario_con_rol("Administración"))
    assert client.post(URL, datos(quitar_logo="on")).status_code == 403
    empresa.refresh_from_db()
    assert empresa.logo and os.path.exists(empresa.logo.path)
