from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from catalogos import servicios
from catalogos.models import Banco, LineaDescompuesto, Partida, Recurso
from core import servicios as servicios_core
from core.models import PermisoRol, Rol
from core.permisos import PARTES
from empresa.models import Seccion

pytestmark = pytest.mark.django_db


@pytest.fixture
def externo(presupuesto_bc3, gerente):
    return servicios.importar_bc3(presupuesto_bc3, "presupuesto_2016.bc3", gerente).banco


def _subir(cliente, datos, nombre="presupuesto_2016.bc3"):
    return cliente.post("/banco/importar/", {"fichero": SimpleUploadedFile(nombre, datos)})


# ── Permisos por rol ───────────────────────────────────────────────────────────


def test_plantillas_con_permisos_del_banco():
    permisos = {
        p.rol.nombre: (p.ver, p.crear, p.editar, p.ver_importes)
        for p in PermisoRol.objects.filter(parte="banco").select_related("rol")
    }
    assert permisos == {
        "Gerencia": (True, True, True, True),
        "Administración": (True, False, False, True),
        "Oficina técnica": (True, True, True, True),
        "Jefe de obra": (True, False, False, True),
    }
    assert "banco" in PARTES and Banco.objects.filter(tipo=Banco.PROPIO).count() == 1


@pytest.mark.parametrize(
    "rol, ve, edita",
    [("Gerencia", True, True), ("Oficina técnica", True, True), ("Administración", True, False),
     ("Jefe de obra", True, False), ("Encargado", False, False), (None, False, False)],
)  # fmt: skip
def test_acceso_por_rol(cliente_con_rol, empresa, rol, ve, edita, externo):
    cliente = cliente_con_rol(rol)
    partida = Partida.objects.get(banco=externo, codigo="ALB010")
    lecturas = ["/banco/", "/banco/recursos/", "/banco/externos/", f"/banco/externos/{externo.pk}/",
                f"/banco/externos/{externo.pk}/partidas/{partida.pk}/"]  # fmt: skip
    for url in lecturas:
        assert cliente.get(url).status_code == (200 if ve else 403), url
    escritura = 302 if edita else 403
    assert cliente.get("/banco/importar/").status_code == (200 if edita else 403)
    copiar = f"/banco/externos/{externo.pk}/partidas/{partida.pk}/copiar/"
    assert cliente.post(copiar).status_code == escritura
    assert ("Banco de precios" in cliente.get("/").content.decode()) is ve


@pytest.fixture
def cliente_sin_importes(client, django_user_model, gerente):
    rol = servicios_core.guardar_rol(Rol(nombre="Consulta banco"), {"banco": {"ver": True}},
                                     None, gerente)  # fmt: skip
    usuario = django_user_model.objects.create_user("consulta", password="x")
    usuario.groups.add(rol.grupo)
    client.force_login(usuario)
    return client


def test_sin_ver_importes_los_precios_salen_ocultos(cliente_sin_importes, empresa, externo,
                                                    gerente):  # fmt: skip
    copia = servicios.copiar_al_banco_propio(
        Partida.objects.get(banco=externo, codigo="ALB010"), gerente
    )
    externa = Partida.objects.get(banco=externo, codigo="ALB010")
    for url in ["/banco/", "/banco/recursos/", f"/banco/externos/{externo.pk}/",
                f"/banco/externos/{externo.pk}/partidas/{externa.pk}/",
                f"/banco/partidas/{copia.pk}/"]:  # fmt: skip
        html = cliente_sin_importes.get(url).content.decode()
        assert "(oculto)" in html, url
        # Precios tal como se pintan (fichas con 4 decimales, listas con 2); el texto largo de
        # la partida habla de «12,40 €/m²» y eso sí se ve: es descripción, no un campo de precio
        for precio in ("12,4000", "22,5000", "22,50", "7,4375", "7,44"):
            assert precio not in html, (url, precio)


# ── Importar BC3 por pantalla ──────────────────────────────────────────────────


def test_importar_con_vista_previa_y_confirmacion(cliente_con_rol, empresa, media_temporal,
                                                  presupuesto_bc3):  # fmt: skip
    cliente = cliente_con_rol("Oficina técnica")
    respuesta = _subir(cliente, presupuesto_bc3)
    html = respuesta.content.decode()
    assert respuesta.status_code == 200 and "Resumen antes de importar" in html
    assert "Se creará un <strong>banco externo nuevo</strong>" in html
    assert "<strong>3</strong> partidas" in html and "<strong>8</strong> recursos" in html
    assert "~L ignorados" in html
    assert not Banco.objects.filter(tipo=Banco.EXTERNO).exists()  # aún no se ha guardado nada

    respuesta = cliente.post("/banco/importar/", {"confirmar": ""})
    banco = Banco.objects.get(tipo=Banco.EXTERNO)
    assert respuesta.status_code == 302 and respuesta.url == f"/banco/externos/{banco.pk}/"
    assert list((media_temporal / "importaciones" / "bc3").iterdir()) == []  # se borra la subida
    assert "ALB010" in cliente.get(respuesta.url).content.decode()

    # Volver a subirlo avisa de que se actualizará
    html = _subir(cliente, presupuesto_bc3).content.decode()
    assert "se actualizará" in html
    cliente.post("/banco/importar/", {"confirmar": ""})
    assert Banco.objects.filter(tipo=Banco.EXTERNO).count() == 1


def test_cancelar_no_guarda_nada(cliente_con_rol, empresa, media_temporal, presupuesto_bc3):
    cliente = cliente_con_rol("Gerencia")
    _subir(cliente, presupuesto_bc3)
    respuesta = cliente.post("/banco/importar/", {"cancelar": ""})
    assert respuesta.status_code == 302 and not Banco.objects.filter(tipo=Banco.EXTERNO).exists()
    assert list((media_temporal / "importaciones" / "bc3").iterdir()) == []


def test_solo_ficheros_bc3(cliente_con_rol, empresa, media_temporal):
    html = _subir(cliente_con_rol("Gerencia"), b"x", "tarifa.xlsx").content.decode()
    assert "extensión .bc3" in html


def test_confirmar_sin_subida(cliente_con_rol, empresa):
    respuesta = cliente_con_rol("Gerencia").post("/banco/importar/", {"confirmar": ""})
    assert respuesta.status_code == 302 and respuesta.url == "/banco/importar/"


# ── Banco externo y copia ──────────────────────────────────────────────────────


def test_contenido_del_banco_externo_sin_filtrar_por_seccion(cliente_con_rol, empresa, externo,
                                                             gerente):  # fmt: skip
    from core.servicios import elegir_seccion

    cliente = cliente_con_rol("Gerencia")
    usuario = cliente.session.get("_auth_user_id")
    from core.models import Usuario

    elegir_seccion(Usuario.objects.get(pk=usuario),
                   Seccion.objects.create(codigo="PT", nombre="Pintura"))  # fmt: skip
    html = cliente.get(f"/banco/externos/{externo.pk}/").content.decode()
    assert "ALB010" in html and "PIN010" in html and "AX1" not in html  # sin auxiliares
    assert "Mostrando:" not in html


def test_ficha_externa_y_copia(cliente_con_rol, empresa, externo):
    cliente = cliente_con_rol("Gerencia")
    partida = Partida.objects.get(banco=externo, codigo="ALB010")
    html = cliente.get(f"/banco/externos/{externo.pk}/partidas/{partida.pk}/").content.decode()
    assert "Copiar al banco propio" in html and "Mortero de cemento M-5" in html
    respuesta = cliente.post(f"/banco/externos/{externo.pk}/partidas/{partida.pk}/copiar/")
    copia = Partida.objects.get(banco=servicios.banco_propio(), codigo="ALB010")
    assert respuesta.status_code == 302 and respuesta.url == f"/banco/partidas/{copia.pk}/"
    html = cliente.get(respuesta.url).content.decode()
    assert "Copiada de Reforma de baño de ejemplo · ALB010" in html
    assert "pendiente" in html and "Historial" in html


def test_jefe_de_obra_ve_la_ficha_externa_sin_boton_de_copiar(cliente_con_rol, empresa, externo):
    partida = Partida.objects.get(banco=externo, codigo="ALB010")
    html = (
        cliente_con_rol("Jefe de obra")
        .get(f"/banco/externos/{externo.pk}/partidas/{partida.pk}/")
        .content.decode()
    )
    assert "Copiar al banco propio" not in html and "Mortero de cemento M-5" in html


# ── Banco propio: ficha con descompuesto editable ──────────────────────────────


@pytest.fixture
def recursos_propios(gerente):
    propio = servicios.banco_propio()
    oficial = servicios.guardar_recurso(
        Recurso(banco=propio, codigo="OF", tipo=Recurso.MANO_OBRA, unidad="h",
                resumen="Oficial", precio=D("20")), gerente)  # fmt: skip
    pintura = servicios.guardar_recurso(
        Recurso(banco=propio, codigo="PP", tipo=Recurso.MATERIAL, unidad="l",
                resumen="Pintura plástica", precio=D("4")), gerente)  # fmt: skip
    return oficial, pintura


def _datos_partida(lineas, codigo="PIN-01", **extra):
    datos = {"codigo": codigo, "resumen": "Pintura plástica en paredes", "unidad": "m2",
             "texto": "", "seccion": "", "lineas-TOTAL_FORMS": str(len(lineas)),
             "lineas-INITIAL_FORMS": "0", "lineas-MIN_NUM_FORMS": "0",
             "lineas-MAX_NUM_FORMS": "1000", **extra}  # fmt: skip
    for i, (elemento, rendimiento, merma) in enumerate(lineas):
        prefijo = f"lineas-{i}-"
        datos |= {prefijo + "elemento": elemento, prefijo + "factor": "1",
                  prefijo + "rendimiento": rendimiento, prefijo + "merma_pct": merma}  # fmt: skip
    return datos


def test_alta_de_partida_con_descompuesto(cliente_con_rol, empresa, recursos_propios):
    oficial, pintura = recursos_propios
    cliente = cliente_con_rol("Oficina técnica")
    respuesta = cliente.post("/banco/partidas/nueva/", _datos_partida(
        [(f"r{oficial.pk}", "0.12", "0"), (f"r{pintura.pk}", "0.3", "10")]
    ))  # fmt: skip
    partida = Partida.objects.get(codigo="PIN-01")
    assert respuesta.status_code == 302 and respuesta.url == f"/banco/partidas/{partida.pk}/"
    # 0,12 × 20 + 0,3 × 1,10 × 4 = 2,40 + 1,32
    assert partida.precio == D("3.7200") and partida.banco.es_propio
    html = cliente.get(respuesta.url).content.decode()
    assert "Pintura plástica" in html and "3,72" in html and "Historial" in html


def test_editar_el_descompuesto_recalcula_y_queda_en_el_historial(
    cliente_con_rol, empresa, recursos_propios
):
    oficial, pintura = recursos_propios
    cliente = cliente_con_rol("Gerencia")
    cliente.post("/banco/partidas/nueva/", _datos_partida([(f"r{oficial.pk}", "0.12", "0")]))
    partida = Partida.objects.get(codigo="PIN-01")
    linea = partida.lineas.get()
    datos = _datos_partida([(f"r{oficial.pk}", "0.15", "0")])
    datos |= {"lineas-INITIAL_FORMS": "1", "lineas-0-id": str(linea.pk),
              "lineas-0-partida": str(partida.pk)}  # fmt: skip
    cliente.post(f"/banco/partidas/{partida.pk}/", datos)
    partida.refresh_from_db()
    assert partida.precio == D("3.0000")
    html = cliente.get(f"/banco/partidas/{partida.pk}/").content.decode()
    assert "Descompuesto · OF" in html and "Rendimiento" in html


def test_linea_sin_elemento_da_error(cliente_con_rol, empresa, recursos_propios):
    respuesta = cliente_con_rol("Gerencia").post(
        "/banco/partidas/nueva/", _datos_partida([("", "0.12", "0")])
    )
    assert respuesta.status_code == 200 and "Elige un recurso" in respuesta.content.decode()
    assert not Partida.objects.filter(codigo="PIN-01").exists()


def test_seccion_del_selector_en_el_alta_y_filtro_en_la_lista(cliente_con_rol, empresa, gerente):
    from core.models import Usuario
    from core.servicios import elegir_seccion

    pt = Seccion.objects.create(codigo="PT", nombre="Pintura")
    pyl = Seccion.objects.create(codigo="PYL", nombre="Pladur")
    propio = servicios.banco_propio()
    servicios.guardar_partida(Partida(banco=propio, codigo="A", resumen="De pintura",
                                      seccion=pt), [], [], gerente)  # fmt: skip
    servicios.guardar_partida(Partida(banco=propio, codigo="B", resumen="De pladur",
                                      seccion=pyl), [], [], gerente)  # fmt: skip
    cliente = cliente_con_rol("Gerencia")
    usuario = Usuario.objects.get(pk=cliente.session["_auth_user_id"])
    elegir_seccion(usuario, pt)
    html = cliente.get("/banco/").content.decode()
    assert "De pintura" in html and "De pladur" not in html and "Mostrando:" in html
    form = cliente.get("/banco/partidas/nueva/").content.decode()
    assert f'value="{pt.pk}" selected' in form


def test_los_externos_no_se_editan_desde_la_ficha_propia(cliente_con_rol, empresa, externo):
    partida = Partida.objects.get(banco=externo, codigo="ALB010")
    assert cliente_con_rol("Gerencia").get(f"/banco/partidas/{partida.pk}/").status_code == 404


def test_recurso_con_precio_nuevo_deja_de_estar_pendiente(cliente_con_rol, empresa, externo,
                                                          gerente):  # fmt: skip
    copia = servicios.copiar_al_banco_propio(
        Partida.objects.get(banco=externo, codigo="PIN010"), gerente
    )
    mo3 = Recurso.objects.get(banco=copia.banco, codigo="MO3")
    assert mo3.pendiente == Recurso.PENDIENTE_COSTE_HORA
    cliente = cliente_con_rol("Gerencia")
    cliente.post(f"/banco/recursos/{mo3.pk}/", {
        "codigo": "MO3", "tipo": "mano_obra", "unidad": "h", "resumen": "Oficial pintor",
        "precio": "25", "fecha_precio": "", "texto": "",
    })  # fmt: skip
    mo3.refresh_from_db()
    copia.refresh_from_db()
    assert mo3.pendiente == "" and mo3.precio == D("25.0000")
    assert copia.precio == D("4.3530")  # 25×0,12 + 4,10×1,1×0,3
    assert LineaDescompuesto.objects.filter(partida=copia).count() == 2
