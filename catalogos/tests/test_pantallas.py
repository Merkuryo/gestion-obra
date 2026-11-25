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
        # Precios tal como se pintan (y como se guardan); el texto largo de
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
    assert "<strong>1</strong> auxiliar</li>" in html  # singular
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
    respuesta = cliente.post("/banco/importar/", {"confirmar": ""}, follow=True)
    assert Banco.objects.filter(tipo=Banco.EXTERNO).count() == 1
    assert ("actualizado: 0 partidas nuevas, 0 actualizadas y 0 quitadas; 0 recursos nuevos, 0 "
            "actualizados y 0 quitados.") in respuesta.content.decode()  # fmt: skip


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


def test_unidad_normalizada_al_guardar_y_aviso_si_no_se_reconoce(cliente_con_rol, empresa,
                                                                 recursos_propios):  # fmt: skip
    cliente = cliente_con_rol("Oficina técnica")
    form = cliente.get("/banco/partidas/nueva/").content.decode()
    assert "Se guarda en su forma normalizada" in form
    respuesta = cliente.post("/banco/partidas/nueva/", _datos_partida([]), follow=True)
    assert Partida.objects.get(codigo="PIN-01").unidad == "m²"
    assert "no está en la lista de unidades" not in respuesta.content.decode()
    datos = _datos_partida([], codigo="PIN-02", unidad=" Bote ")
    respuesta = cliente.post("/banco/partidas/nueva/", datos, follow=True)
    assert Partida.objects.get(codigo="PIN-02").unidad == "Bote"
    assert "La unidad «Bote» no está en la lista de unidades" in respuesta.content.decode()


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


# ── Pantalla: precios con 2 decimales y aviso de partida sin sección ───────────


def test_precios_con_2_decimales_y_rendimientos_con_4(cliente_con_rol, empresa, externo, gerente):
    copia = servicios.copiar_al_banco_propio(
        Partida.objects.get(banco=externo, codigo="ALB010"), gerente
    )
    cliente = cliente_con_rol("Gerencia")
    html = cliente.get(f"/banco/partidas/{copia.pk}/").content.decode()
    assert "7,44</strong> / m²" in html and "7,4375" not in html  # precio 7,4375 guardado
    assert "22,50" in html and "22,5000" not in html  # precio del recurso en el descompuesto
    assert "0,2500" in html  # rendimiento con 4 decimales
    externa = Partida.objects.get(banco=externo, codigo="ALB010")
    html = cliente.get(f"/banco/externos/{externo.pk}/partidas/{externa.pk}/").content.decode()
    assert "12,40</span>" in html and "12,4000" not in html


def test_precio_unitario_de_recursos_con_sus_decimales(cliente_con_rol, empresa, gerente):
    propio = servicios.banco_propio()
    tornillo = servicios.guardar_recurso(
        Recurso(banco=propio, codigo="TOR", tipo=Recurso.MATERIAL, unidad="ud",
                resumen="Tornillo", precio=D("0.0120")), gerente)  # fmt: skip
    oficial = servicios.guardar_recurso(
        Recurso(banco=propio, codigo="OF", tipo=Recurso.MANO_OBRA, unidad="h",
                resumen="Oficial", precio=D("23.6250")), gerente)  # fmt: skip
    partida = servicios.guardar_partida(
        Partida(banco=propio, codigo="FIJ", unidad="ud", resumen="Fijación"),
        [LineaDescompuesto(recurso=tornillo, rendimiento=D("4")),
         LineaDescompuesto(recurso=oficial, rendimiento=D("0.0100"))], [], gerente,
    )  # fmt: skip
    cliente = cliente_con_rol("Gerencia")

    lista = " ".join(cliente.get("/banco/recursos/").content.decode().split())
    assert "0,012 </td>" in lista and "23,625 </td>" in lista and "0,01 </td>" not in lista

    ficha = cliente.get(f"/banco/recursos/{tornillo.pk}/").content.decode()
    assert 'value="0.012"' in ficha  # el campo del formulario
    historial = ficha[ficha.index("Historial") :]
    assert "0,012" in historial and "0,01<" not in historial

    # En el descompuesto: el precio del recurso con sus decimales; importes y partida, con 2
    html = cliente.get(f"/banco/partidas/{partida.pk}/").content.decode()
    assert "0,012</td>" in html and "23,625</td>" in html
    assert "0,05</td>" in html  # 4 × 0,012 = 0,048 → 0,05
    assert partida.precio == D("0.2843") and "0,28</strong> / ud" in html  # 0,048 + 0,23625


def test_historial_con_precios_a_2_decimales(cliente_con_rol, empresa, externo, gerente):
    copia = servicios.copiar_al_banco_propio(
        Partida.objects.get(banco=externo, codigo="ALB010"), gerente
    )
    html = cliente_con_rol("Gerencia").get(f"/banco/partidas/{copia.pk}/").content.decode()
    historial = html[html.index("Historial") :]
    assert "Alta" in historial and "Copiada de Reforma de baño de ejemplo" in historial
    assert "7,44" in historial and "7,4375" not in historial


def test_campo_precio_del_recurso_sin_ceros_de_mas(cliente_con_rol, empresa, recursos_propios):
    oficial, _ = recursos_propios
    html = cliente_con_rol("Gerencia").get(f"/banco/recursos/{oficial.pk}/").content.decode()
    assert 'value="20.00"' in html and "20.0000" not in html


def test_aviso_en_la_ficha_si_la_partida_no_tiene_seccion(cliente_con_rol, empresa, externo,
                                                          gerente):  # fmt: skip
    externa = Partida.objects.get(banco=externo, codigo="ALB010")
    sin = servicios.copiar_al_banco_propio(externa, gerente)  # copiada con «Todas»
    con = servicios.copiar_al_banco_propio(
        externa, gerente, seccion=Seccion.objects.create(codigo="AL", nombre="Albañilería")
    )
    cliente = cliente_con_rol("Gerencia")
    aviso = "Esta partida no tiene sección"
    assert aviso in cliente.get(f"/banco/partidas/{sin.pk}/").content.decode()
    assert aviso not in cliente.get(f"/banco/partidas/{con.pk}/").content.decode()


# ── Importar la plantilla de cálculo por pantalla ──────────────────────────────


def _subir_plantilla(cliente, datos, nombre="calculo.xlsm"):
    return cliente.post("/banco/importar/plantilla/",
                        {"fichero": SimpleUploadedFile(nombre, datos)})  # fmt: skip


def _elecciones(**extra):
    datos = {"num_partidas": "4", "coste_dia": "189,00"}
    for i in range(4):
        datos[f"importar_{i}"] = "on"
    return datos | extra


def test_plantilla_con_vista_previa_recalculo_y_confirmacion(cliente_con_rol, empresa,
                                                             media_temporal):  # fmt: skip
    from importadores.tests.plantilla_muestra import crear_plantilla

    cliente = cliente_con_rol("Oficina técnica")
    html = _subir_plantilla(cliente, crear_plantilla()).content.decode()
    assert "Resumen antes de importar" in html and "PINTURA INTERIOR" in html
    assert "provisional" in html and 'value="189,00"' in html
    assert "90,0000" in html and "0,0889" in html  # rendimiento y horas por unidad
    assert "1.700,00 €" in html  # lección del resumen duplicado
    assert "Ejecto pizarra" in html and "Son el mismo" in html
    assert not Partida.objects.filter(banco=servicios.banco_propio()).exists()

    # Coste día cambiado y una hoja fuera: se recalcula sin guardar
    datos = _elecciones(coste_dia="200", recalcular="")
    del datos["importar_3"]
    html = cliente.post("/banco/importar/plantilla/", datos).content.decode()
    assert 'value="200,00"' in html and "95,2381" in html and "no se importa" in html
    assert not Partida.objects.filter(banco=servicios.banco_propio()).exists()

    datos["confirmar"] = ""
    del datos["recalcular"]
    respuesta = cliente.post("/banco/importar/plantilla/", datos)
    assert respuesta.status_code == 302 and respuesta.url == "/banco/"
    assert Partida.objects.filter(banco=servicios.banco_propio()).count() == 3
    assert Recurso.objects.get(codigo="MO-OPERARIO").precio == D("25.0000")
    assert list((media_temporal / "importaciones" / "plantillas").iterdir()) == []

    ficha = Partida.objects.get(resumen="PINTURA INTERIOR")
    html = cliente.get(f"/banco/partidas/{ficha.pk}/").content.decode()
    assert "Importada de la plantilla de cálculo · pintura interior" in html
    assert "Mano de obra en la plantilla" in html and "2,10" in html
    assert "Esta partida no tiene sección" in html


def test_plantilla_hoja_que_ya_existe_hay_que_elegir(cliente_con_rol, empresa, media_temporal):
    from importadores.tests.plantilla_muestra import PARTIDAS, crear_plantilla

    cliente = cliente_con_rol("Gerencia")
    _subir_plantilla(cliente, crear_plantilla())
    cliente.post("/banco/importar/plantilla/", _elecciones(confirmar=""))
    cambiadas = [("PINTURA INTERIOR", 100, 4.2, [("Pintura plástica blanca", 3.0, 0, 0.3)], 0, 0,
                  500), *PARTIDAS[1:]]  # fmt: skip
    html = _subir_plantilla(cliente, crear_plantilla(partidas=cambiadas)).content.decode()
    assert "Ya existe en el banco" in html and "falta elegir" in html and "Elige…" in html
    assert 'name="accion_0"' in html and 'name="importar_0"' not in html
    assert '<option value="omitir" selected>' in html  # las que no cambian
    assert "% beneficio" in html and "Crear otra (código nuevo)" in html
    # Precio actual en el banco frente al de la plantilla, y qué cambia
    assert "Precio actual en el banco" in html and "Precio con la plantilla" in html
    assert "2,93" in html and "5,19" in html
    assert "Qué cambia si se actualiza" in html and "rendimiento 0,2500 → 0,3000" in html
    assert "mano de obra en la plantilla 2,10 → 4,20 €/ud" in html

    # Sin elegir no importa: vuelve a la vista previa con el error
    datos = {
        "num_partidas": "4",
        "coste_dia": "189,00",
        "accion_0": "",
        "accion_1": "omitir",
        "accion_2": "omitir",
        "accion_3": "omitir",
        "confirmar": "",
    }
    respuesta = cliente.post("/banco/importar/plantilla/", datos)
    assert respuesta.status_code == 200 and "ha cambiado; elige" in respuesta.content.decode()
    assert Partida.objects.get(codigo="P-001").precio_mo_referencia == D("2.1000")

    datos["accion_0"] = "actualizar"
    respuesta = cliente.post("/banco/importar/plantilla/", datos, follow=True)
    assert respuesta.redirect_chain[0][1] == 302
    assert ("Plantilla importada: 0 partidas nuevas, 1 actualizada y 0 sin cambios; 0 recursos "
            "nuevos.") in respuesta.content.decode()  # fmt: skip
    assert Partida.objects.get(codigo="P-001").precio_mo_referencia == D("4.2000")
    assert Partida.objects.filter(banco=servicios.banco_propio()).count() == 4


def test_plantilla_con_codigo_repetido_vuelve_a_la_vista_previa(cliente_con_rol, empresa,
                                                                media_temporal):  # fmt: skip
    from importadores.tests.plantilla_muestra import crear_plantilla

    cliente = cliente_con_rol("Gerencia")
    _subir_plantilla(cliente, crear_plantilla())
    datos = _elecciones(codigo_0="X", codigo_1="X", confirmar="")
    respuesta = cliente.post("/banco/importar/plantilla/", datos)
    assert respuesta.status_code == 200 and "se repite" in respuesta.content.decode()
    assert not Partida.objects.filter(banco=servicios.banco_propio()).exists()


def test_plantilla_cancelar_y_extension(cliente_con_rol, empresa, media_temporal):
    from importadores.tests.plantilla_muestra import crear_plantilla

    cliente = cliente_con_rol("Gerencia")
    _subir_plantilla(cliente, crear_plantilla())
    respuesta = cliente.post("/banco/importar/plantilla/", {"cancelar": ""})
    assert respuesta.status_code == 302
    assert list((media_temporal / "importaciones" / "plantillas").iterdir()) == []
    html = _subir_plantilla(cliente, b"x", "banco.bc3").content.decode()
    assert ".xlsx o .xlsm" in html
    respuesta = cliente.post("/banco/importar/plantilla/", _elecciones(confirmar=""))
    assert respuesta.status_code == 302 and respuesta.url == "/banco/importar/plantilla/"


@pytest.mark.parametrize("rol, codigo", [("Gerencia", 200), ("Oficina técnica", 200),
                                         ("Jefe de obra", 403), ("Administración", 403),
                                         ("Encargado", 403)])  # fmt: skip
def test_importar_plantilla_solo_quien_edita_el_banco(cliente_con_rol, empresa, rol, codigo):
    cliente = cliente_con_rol(rol)
    assert cliente.get("/banco/importar/plantilla/").status_code == codigo
    assert cliente.post("/banco/importar/plantilla/", {"cancelar": ""}).status_code in (
        (302,) if codigo == 200 else (403,)
    )


def test_plantilla_sin_ver_importes_oculta_precios_y_coste_dia(client, django_user_model,
                                                               gerente, empresa,
                                                               media_temporal):  # fmt: skip
    from importadores.tests.plantilla_muestra import crear_plantilla

    rol = servicios_core.guardar_rol(
        Rol(nombre="Edita sin importes"),
        {"banco": {"ver": True, "crear": True, "editar": True}}, None, gerente,
    )  # fmt: skip
    usuario = django_user_model.objects.create_user("sin_importes", password="x")
    usuario.groups.add(rol.grupo)
    client.force_login(usuario)
    html = _subir_plantilla(client, crear_plantilla()).content.decode()
    assert "PINTURA INTERIOR" in html and 'name="coste_dia"' not in html
    assert "Precio en el banco" not in html and "(oculto)" in html
    # El coste día no se puede cambiar sin verlo: se usa el de la plantilla
    client.post("/banco/importar/plantilla/", _elecciones(coste_dia="500", confirmar=""))
    assert Recurso.objects.get(codigo="MO-OPERARIO").precio == D("23.6250")
