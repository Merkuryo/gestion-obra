import json
import time
from decimal import Decimal as D

import pytest
from django.db import connection
from django.http import QueryDict

from catalogos import busqueda, servicios
from catalogos.models import Banco, Capitulo, Partida
from core import servicios as servicios_core
from core.models import Rol
from empresa.models import Seccion

pytestmark = pytest.mark.django_db


def _buscar(usuario, q="", **filtros):
    filtros.setdefault("seccion", busqueda.TODAS)
    return busqueda.buscar(usuario, busqueda.Filtros(q=q, **filtros))


def _codigos(resultado):
    return [p.codigo for p in resultado.partidas]


@pytest.fixture
def secciones(empresa):
    return (Seccion.objects.create(codigo="PT", nombre="Pintura"),
            Seccion.objects.create(codigo="ALB", nombre="Albañilería"))  # fmt: skip


@pytest.fixture
def bancos(empresa, secciones):
    """Banco propio con tres partidas y dos bancos externos con capítulos."""
    pt, alb = secciones
    propio = servicios.banco_propio()
    Partida.objects.bulk_create([
        Partida(banco=propio, codigo="P-001", unidad="m²", resumen="PLÁSTICA INTERIOR",
                precio=D("2.95"), seccion=pt),
        Partida(banco=propio, codigo="P-002", unidad="m²", resumen="Esmalte sintético",
                texto="Dos manos sobre imprimación.", precio=D("16.88"), seccion=pt),
        Partida(banco=propio, codigo="P-003", unidad="ml", resumen="Líneas de parking",
                precio=D("1.20"), seccion=alb),
        Partida(banco=propio, codigo="AUX-1", unidad="m²", resumen="Plástica base auxiliar",
                precio=D("1.00"), auxiliar=True),
    ])  # fmt: skip
    obras = Banco.objects.create(tipo=Banco.EXTERNO, nombre="Banco de obras", clave="a")
    acabados = Capitulo.objects.create(banco=obras, codigo="03#", resumen="Acabados y pinturas")
    Partida.objects.bulk_create([
        Partida(banco=obras, capitulo=acabados, codigo="PIN010", unidad="m²",
                resumen="Pintura plástica lisa en paramentos interiores", precio=D("5.60")),
        Partida(banco=obras, capitulo=acabados, codigo="PIN020", unidad="m²",
                resumen="Revestimiento de paramentos",
                texto="Acabado con pintura plástica mate en dos manos.", precio=D("7.10")),
    ])  # fmt: skip
    reformas = Banco.objects.create(tipo=Banco.EXTERNO, nombre="Banco de reformas", clave="b")
    Partida.objects.create(
        banco=reformas,
        codigo="ALB010",
        unidad="m²",
        resumen="Tabique de ladrillo hueco doble",
        precio=D("21.40"),
    )
    return propio, obras, reformas


# ── Texto: tildes, erratas, raíz, relevancia ───────────────────────────────────


@pytest.mark.parametrize("q", ["plastica", "plástica", "PLASTICA", "Plástica", "plastca"])
def test_con_y_sin_tildes_y_con_erratas(bancos, gerente, q):
    assert "P-001" in _codigos(_buscar(gerente, q))


@pytest.mark.parametrize("q, codigo", [("pintra", "PIN010"), ("esmlte", "P-002"),
                                       ("ladriyo", "ALB010"), ("tabiq", "ALB010")])  # fmt: skip
def test_erratas_y_palabras_a_medias(bancos, gerente, q, codigo):
    assert codigo in _codigos(_buscar(gerente, q))


def test_por_la_raiz_de_la_palabra(bancos, gerente):
    assert "PIN010" in _codigos(_buscar(gerente, "pinturas"))  # pintura
    assert "P-003" in _codigos(_buscar(gerente, "linea"))  # Líneas


def test_relevancia_resumen_antes_que_capitulo_y_texto_largo(bancos, gerente):
    resultado = _buscar(gerente, "pintura plastica", bancos=[str(bancos[1].pk)])
    # PIN010 lo dice en el resumen; PIN020 solo en el texto largo
    assert _codigos(resultado) == ["PIN010", "PIN020"]
    assert resultado.partidas[0].relevancia > resultado.partidas[1].relevancia


def test_codigo_exacto_primero_y_codigos_por_su_principio(bancos, gerente):
    assert _codigos(_buscar(gerente, "P-002"))[0] == "P-002"
    assert _codigos(_buscar(gerente, "p-00")) == ["P-001", "P-002", "P-003"]
    assert _codigos(_buscar(gerente, "PIN0")) == ["PIN010", "PIN020"]


def test_todas_las_palabras_cuentan_menos_las_vacias(bancos, gerente):
    resultado = _buscar(gerente, "pintura de paramentos")
    assert resultado.ignoradas == ["de"]
    assert set(_codigos(resultado)) == {"PIN010", "PIN020"}
    assert _codigos(_buscar(gerente, "pintura ladrillo")) == []


def test_busca_en_el_texto_largo_y_en_el_capitulo(bancos, gerente):
    assert _codigos(_buscar(gerente, "imprimacion")) == ["P-002"]
    assert set(_codigos(_buscar(gerente, "acabados"))) == {"PIN010", "PIN020"}


def test_se_actualiza_al_guardar_y_al_cambiar_el_capitulo(bancos, gerente):
    partida = Partida.objects.get(codigo="P-001")
    partida.resumen = "Pintura al silicato"
    servicios.guardar_partida(partida, [], [], gerente)
    assert "P-001" not in _codigos(_buscar(gerente, "interior"))
    assert "P-001" in _codigos(_buscar(gerente, "silicato"))

    capitulo = Capitulo.objects.get(codigo="03#")
    capitulo.resumen = "Revestimientos continuos"
    capitulo.save()
    assert set(_codigos(_buscar(gerente, "continuos"))) == {"PIN010", "PIN020"}
    assert _codigos(_buscar(gerente, "acabados")) == ["PIN020"]  # su texto dice «Acabado»


def test_tambien_lo_importado_de_bc3(empresa, gerente, presupuesto_bc3):
    servicios.importar_bc3(presupuesto_bc3, "presupuesto_2016.bc3", gerente)
    assert "ALB010" in _codigos(_buscar(gerente, "enlucido yeso"))


# ── Filtros ────────────────────────────────────────────────────────────────────


def test_filtro_por_banco(bancos, gerente):
    propio, obras, reformas = bancos
    assert _codigos(_buscar(gerente, bancos=["propio"])) == ["P-001", "P-002", "P-003"]
    assert _codigos(_buscar(gerente, bancos=[str(obras.pk), str(reformas.pk)])) == [
        "PIN010", "PIN020", "ALB010"
    ]  # fmt: skip
    assert len(_codigos(_buscar(gerente, bancos=[]))) == 6  # todos, sin auxiliares


def test_auxiliares_solo_si_se_piden(bancos, gerente):
    assert "AUX-1" not in _codigos(_buscar(gerente, "plastica"))
    assert "AUX-1" in _codigos(_buscar(gerente, "plastica", auxiliares=True))


def test_filtro_por_seccion_y_seccion_de_trabajo(bancos, gerente, secciones):
    pt, alb = secciones
    # Las externas no tienen sección: no se filtran
    assert _codigos(_buscar(gerente, seccion=str(alb.pk))) == [
        "P-003", "PIN010", "PIN020", "ALB010"
    ]  # fmt: skip
    servicios_core.elegir_seccion(gerente, pt)
    gerente.refresh_from_db()
    resultado = busqueda.buscar(gerente, busqueda.Filtros(bancos=["propio"]))
    assert resultado.seccion == pt and _codigos(resultado) == ["P-001", "P-002"]
    assert len(_codigos(_buscar(gerente, bancos=["propio"]))) == 3  # «todas»


@pytest.mark.parametrize("unidad, codigos", [
    ("m2", ["P-001", "P-002", "PIN010", "PIN020", "ALB010"]), ("M²", None),
    ("ml", ["P-003"]), ("m", ["P-003"]), ("M.L.", ["P-003"]), ("ud", []),
])  # fmt: skip
def test_filtro_por_unidad_con_sus_equivalencias(bancos, gerente, unidad, codigos):
    esperado = codigos if codigos is not None else ["P-001", "P-002", "PIN010", "PIN020", "ALB010"]
    assert _codigos(_buscar(gerente, unidad=unidad)) == esperado


def test_filtro_por_capitulo_sin_tildes(bancos, gerente):
    assert _codigos(_buscar(gerente, capitulo="pinturas")) == ["PIN010", "PIN020"]
    assert _codigos(_buscar(gerente, capitulo="03#")) == ["PIN010", "PIN020"]


def test_filtro_por_precio_solo_con_ver_importes(bancos, gerente, django_user_model):
    resultado = _buscar(gerente, precio_min=D("5"), precio_max=D("20"))
    assert _codigos(resultado) == ["P-002", "PIN010", "PIN020"]
    rol = servicios_core.guardar_rol(Rol(nombre="Ve el banco"), {"banco": {"ver": True}}, None,
                                     gerente)  # fmt: skip
    usuario = django_user_model.objects.create_user("solo_ver", password="x")
    usuario.groups.add(rol.grupo)
    # Sin ver importes no filtra por precio (si no, se podría adivinar)
    assert len(_codigos(_buscar(usuario, precio_min=D("5"), precio_max=D("20")))) == 6


def test_filtros_desde_la_peticion():
    datos = QueryDict("q=+pintura+&banco=propio&banco=7&banco=x&precio_min=1.234,5&pagina=0")
    filtros = busqueda.Filtros.de_peticion(datos)
    assert (filtros.q, filtros.bancos, filtros.precio_min, filtros.pagina) == (
        "pintura", ["propio", "7"], D("1234.5"), 1
    )  # fmt: skip


# ── Resaltado ──────────────────────────────────────────────────────────────────


def test_resaltado_sin_tildes_erratas_y_escapado():
    terminos = ["plastca", "interior"]
    assert busqueda.resaltar("PLÁSTICA <b>interiores</b>", terminos) == (
        "<mark>PLÁSTICA</mark> &lt;b&gt;<mark>interiores</mark>&lt;/b&gt;"
    )
    texto = "Preparación del soporte. " * 10 + "Acabado con pintura plástica mate."
    trozo = busqueda.fragmento(texto, ["plastica"])
    assert trozo.startswith("… ") and "<mark>plástica</mark>" in trozo
    assert busqueda.fragmento("Nada que ver", ["plastica"]) == ""


# ── Rendimiento ────────────────────────────────────────────────────────────────


def test_rapido_con_un_banco_grande(empresa, gerente):
    """30.000 partidas generadas en la base de datos: cada búsqueda, en menos de 1 s."""
    banco = Banco.objects.create(tipo=Banco.EXTERNO, nombre="Banco grande", clave="grande")
    palabras = ["pintura", "plástica", "esmalte", "tabique", "ladrillo", "enlucido", "yeso",
                "solado", "gres", "alicatado", "azulejo", "cubierta", "teja", "aislamiento",
                "lana", "mineral", "carpintería", "aluminio", "vidrio", "fontanería"]  # fmt: skip
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO catalogos_partida (banco_id, codigo, unidad, resumen, texto, precio,
                auxiliar, origen, creado_en, modificado_en, texto_busqueda)
            SELECT %s, 'G' || lpad(n::text, 6, '0'), 'm²',
                   initcap(p[1 + n %% 20]) || ' ' || p[1 + (n / 20) %% 20] || ' tipo ' || n,
                   'Descripción larga de la partida ' || n || ' con ' || p[1 + (n / 7) %% 20],
                   (n %% 500) / 10.0, false, '', now(), now(), ''
            FROM generate_series(1, 30000) AS n, (SELECT %s::text[] AS p) AS lista
            """,
            [banco.pk, palabras],
        )
        cursor.execute("ANALYZE catalogos_partida")
    Partida.objects.create(banco=banco, codigo="Z1", unidad="m²", resumen="Pintura PLÁSTICA lisa")
    _buscar(gerente, "calentar")  # la primera consulta carga diccionarios y catálogos
    for q in ["plastica lisa", "plastca", "ladriyo yeso", "G01234", "pintura", "carpinteria"]:
        tiempos = []
        for _ in range(2):  # el mejor de dos, para no medir un parón de la máquina
            inicio = time.perf_counter()
            resultado = _buscar(gerente, q)
            tiempos.append(time.perf_counter() - inicio)
        assert resultado.total > 0, q
        assert min(tiempos) < 1, (q, tiempos)
    assert _codigos(_buscar(gerente, "plastica lisa"))[0] == "Z1"


# ── Permisos ───────────────────────────────────────────────────────────────────


def test_pantalla_buscar(bancos, cliente_con_rol):
    cliente = cliente_con_rol("Oficina técnica")
    html = cliente.get("/banco/buscar/?q=plastca&seccion=todas").content.decode()
    assert "Buscar partidas" in html and "<mark>PLÁSTICA</mark>" in html
    assert "2,95" in html and "Copiar al banco propio" in html
    assert 'href="/banco/externos/' in html and "Precio entre" in html
    # Con HTMX solo los resultados
    parcial = cliente.get("/banco/buscar/?q=ladrillo", HTTP_HX_REQUEST="true",
                          HTTP_HX_TARGET="resultados").content.decode()  # fmt: skip
    assert "<!doctype" not in parcial.lower() and "ALB010" in parcial and "1 partida<" in parcial


def test_buscar_sin_permiso_del_banco(cliente_con_rol):
    assert cliente_con_rol(None).get("/banco/buscar/").status_code == 403


def test_sin_ver_importes_ni_editar(bancos, client, django_user_model, gerente):
    rol = servicios_core.guardar_rol(Rol(nombre="Ve el banco"), {"banco": {"ver": True}}, None,
                                     gerente)  # fmt: skip
    usuario = django_user_model.objects.create_user("solo_ver", password="x")
    usuario.groups.add(rol.grupo)
    client.force_login(usuario)
    html = client.get("/banco/buscar/?q=plastica&seccion=todas").content.decode()
    assert "PLÁSTICA" in html and "(oculto)" in html and "2,95" not in html
    assert "Copiar al banco propio" not in html and "Precio entre" not in html


# ── Selector de partida ────────────────────────────────────────────────────────


def test_selector_marca_las_de_otra_unidad(bancos, cliente_con_rol):
    cliente = cliente_con_rol("Oficina técnica")
    datos = {"nombre": "partida", "unidad_destino": "m", "partida_buscar": "parking"}
    html = cliente.get("/banco/selector/", datos).content.decode()
    assert "P-003" in html and "otra unidad" not in html  # ml = m
    datos = {"nombre": "partida", "unidad_destino": "ml", "partida_buscar": "plastica"}
    html = cliente.get("/banco/selector/", datos).content.decode()
    assert "P-001" in html and "m² · otra unidad" in html
    assert 'hx-target="#selector-partida"' in html
    vacio = cliente.get("/banco/selector/", {"nombre": "partida"}).content.decode()
    assert "list-group" not in vacio


def test_selector_devuelve_la_elegida(bancos, cliente_con_rol):
    cliente = cliente_con_rol("Oficina técnica")
    p001 = Partida.objects.get(codigo="P-001")
    datos = {"nombre": "linea_3", "unidad_destino": "ml", "elegida": p001.pk}
    respuesta = cliente.get("/banco/selector/componente/", datos)
    html = respuesta.content.decode()
    assert f'<input type="hidden" name="linea_3" value="{p001.pk}">' in html
    assert "Otra unidad: m² frente a ml" in html
    evento = json.loads(respuesta["HX-Trigger"])["partida-elegida"]
    assert evento == {"nombre": "linea_3", "pk": p001.pk, "codigo": "P-001", "unidad": "m²",
                      "misma_unidad": False}  # fmt: skip
    # «Cambiar» vuelve a la búsqueda
    html = cliente.get("/banco/selector/componente/", {"nombre": "linea_3"}).content.decode()
    assert 'name="linea_3_buscar"' in html and 'value=""' in html
    assert cliente.get("/banco/selector/componente/", {"elegida": "999999"}).status_code == 404


@pytest.mark.urls("catalogos.tests.urls_selector")
def test_selector_en_un_formulario_de_otra_pantalla(bancos, cliente_con_rol):
    cliente = cliente_con_rol("Oficina técnica")
    html = cliente.get("/prueba/selector/").content.decode()
    assert 'id="selector-partida"' in html and "unidad de la línea: ml" in html
    p003 = Partida.objects.get(codigo="P-003")
    respuesta = cliente.post("/prueba/selector/", {"partida": p003.pk})
    assert respuesta.content.decode() == "Elegida: P-003 (misma unidad)"
    respuesta = cliente.post("/prueba/selector/", {"partida": "999999"})
    assert respuesta.status_code == 400 and "ya no existe" in respuesta.content.decode()


def test_selector_primero_la_seccion_de_trabajo(bancos, secciones, usuario_con_rol, client):
    pt, alb = secciones
    propio = bancos[0]
    # Dos partidas iguales para la búsqueda, una de cada sección
    Partida.objects.bulk_create([
        Partida(banco=propio, codigo="Q-PT", unidad="m²", resumen="Rejilla metálica", seccion=pt),
        Partida(banco=propio, codigo="Q-ALB", unidad="m²", resumen="Rejilla metálica", seccion=alb),
    ])  # fmt: skip
    usuario = usuario_con_rol("Oficina técnica")
    servicios_core.elegir_seccion(usuario, alb)
    client.force_login(usuario)
    datos = {"nombre": "partida", "partida_buscar": "rejilla"}
    html = client.get("/banco/selector/", datos).content.decode()
    # Busca en todas las secciones, pero la de trabajo va primero
    assert html.index("Q-ALB") < html.index("Q-PT")
    servicios_core.elegir_seccion(usuario, pt)
    html = client.get("/banco/selector/", datos).content.decode()
    assert html.index("Q-PT") < html.index("Q-ALB")
    # La pantalla de búsqueda con «Todas» no cambia el orden (por código)
    usuario.refresh_from_db()
    resultado = _buscar(usuario, "rejilla")
    assert _codigos(resultado) == ["Q-ALB", "Q-PT"]
