from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from catalogos import servicios
from catalogos.models import LineaDescompuesto, Partida, Recurso
from empresa.models import Seccion
from importadores.tests.plantilla_muestra import PARTIDAS, crear_plantilla

pytestmark = pytest.mark.django_db

FICHERO = "calculo_muestra.xlsx"


@pytest.fixture
def plantilla():
    return crear_plantilla()


def _importar(datos, actor, **opciones):
    return servicios.importar_plantilla(datos, FICHERO, actor, **opciones)


def _propia(codigo=None, resumen=None):
    filtro = {"codigo": codigo} if codigo else {"resumen": resumen}
    return Partida.objects.get(banco=servicios.banco_propio(), **filtro)


def _lineas(partida):
    return [(ln.recurso.codigo, ln.rendimiento, ln.merma_pct) for ln in partida.lineas.all()]


# ── Vista previa ───────────────────────────────────────────────────────────────


def test_vista_previa_no_guarda_nada(plantilla, empresa):
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    assert [(p.hoja.nombre, p.codigo, p.unidad, p.estado) for p in vista.partidas] == [
        ("PINTURA INTERIOR", "P-001", "m²", "nueva"), ("ESMALTE METAL", "P-002", "m²", "nueva"),
        ("EFECTO PIZARRA", "P-003", "m²", "nueva"), ("BARNIZ MADERA", "P-004", "m²", "nueva"),
    ]  # fmt: skip
    assert vista.coste_dia == D("189") and vista.coste_dia_provisional
    assert vista.jornada == D("8.00") and vista.coste_hora == D("23.6250")
    assert vista.se_puede_importar and vista.recuento["nuevas"] == 4
    assert not Partida.objects.filter(banco=servicios.banco_propio()).exists()
    assert not Recurso.objects.exists()


# ── Importación ────────────────────────────────────────────────────────────────


def test_partida_con_mano_de_obra_en_horas_y_materiales(plantilla, empresa, gerente):
    resumen = _importar(plantilla, gerente)
    assert len(resumen.creadas) == 4 and resumen.recursos_creados == 8
    pintura = _propia("P-001")
    assert (pintura.resumen, pintura.unidad, pintura.seccion) == ("PINTURA INTERIOR", "m²", None)
    assert pintura.origen == "Plantilla de cálculo · PINTURA INTERIOR"
    # Mano de obra: 2,10 €/m² con 189 €/día → 90 m²/día → 8 / 90 = 0,0889 h/m²
    assert _lineas(pintura) == [("M-001", D("0.2500"), D("10.00")),
                                ("MO-OPERARIO", D("0.0889"), D("0.00"))]  # fmt: skip
    assert pintura.precio_mo_referencia == D("2.1000")
    # 0,25 × 1,10 × 3 + 0,0889 × 23,625 = 0,8250 + 2,1003
    assert pintura.precio == D("2.9253")

    mo = Recurso.objects.get(codigo="MO-OPERARIO")
    assert (mo.tipo, mo.unidad, mo.precio) == (Recurso.MANO_OBRA, "h", D("23.6250"))
    assert mo.pendiente == Recurso.PENDIENTE_COSTE_HORA  # coste día provisional

    material = Recurso.objects.get(codigo="M-001")
    assert (material.resumen, material.tipo, material.precio) == (
        "Pintura plástica blanca", Recurso.MATERIAL, D("3.0000"),
    )  # fmt: skip
    assert material.pendiente == Recurso.PENDIENTE_TARIFA


def test_fijaciones_transportes_y_descuento(plantilla, empresa, gerente):
    _importar(plantilla, gerente)
    esmalte = _propia("P-002")
    assert _lineas(esmalte) == [
        ("M-002", D("0.2000"), D("10.00")), ("M-003", D("0.1500"), D("10.00")),
        ("FIJACIONES", D("0.5000"), D("0.00")), ("TRANSPORTES", D("0.2500"), D("0.00")),
        ("MO-OPERARIO", D("0.5460"), D("0.00")),
    ]  # fmt: skip
    assert Recurso.objects.get(codigo="M-002").precio == D("7.2000")  # 8 € con 10 % de dto
    assert Recurso.objects.get(codigo="FIJACIONES").precio == D("1.0000")
    # 1,5840 + 1,6500 + 0,50 + 0,25 + 0,5460 × 23,625 (12,8993)
    assert esmalte.precio == D("16.8833")


def test_lecciones_3_y_4_el_precio_no_lleva_gastos_de_empresa_beneficio_ni_desplazamiento(
    plantilla, empresa, gerente
):
    _importar(plantilla, gerente)
    pintura = _propia("P-001")
    # Coste directo de la plantilla: 0,83 de material + 2,10 de mano de obra. La venta de la
    # hoja (4,00) lleva además el 14 % de gastos de empresa, el beneficio y el desplazamiento.
    assert pintura.precio.quantize(D("0.01")) == D("2.93")
    tipos = {ln.recurso.tipo for ln in pintura.lineas.all()}
    assert tipos == {Recurso.MATERIAL, Recurso.MANO_OBRA}


def test_coste_dia_cambiado_en_la_vista_previa(plantilla, empresa, gerente):
    _importar(plantilla, gerente, coste_dia=D("200"))
    pintura = _propia("P-001")
    # 200 / 2,10 = 95,2381 m²/día → 8 × 2,10 / 200 = 0,084 h/m² a 25 €/h = 2,10 €/m²
    assert _lineas(pintura)[-1] == ("MO-OPERARIO", D("0.0840"), D("0.00"))
    assert Recurso.objects.get(codigo="MO-OPERARIO").precio == D("25.0000")
    assert pintura.precio == D("0.8250") + D("2.1000")


def test_con_coste_hora_de_la_empresa(plantilla, empresa, gerente, monkeypatch):
    monkeypatch.setattr(servicios, "coste_hora_actual", lambda: D("30"))
    vista = servicios.preparar_plantilla(plantilla, FICHERO, coste_dia=D("150"))
    assert vista.coste_dia == D("240.00") and not vista.coste_dia_provisional  # 30 × 8
    _importar(plantilla, gerente)
    mo = Recurso.objects.get(codigo="MO-OPERARIO")
    assert (mo.precio, mo.pendiente) == (D("30.0000"), "")
    assert _lineas(_propia("P-001"))[-1][1] == D("0.0700")  # 8 × 2,10 / 240


def test_misma_hoja_otra_vez_no_duplica(plantilla, empresa, gerente):
    _importar(plantilla, gerente)
    antes = (Partida.objects.count(), Recurso.objects.count(), LineaDescompuesto.objects.count(),
             Partida.history.count(), LineaDescompuesto.history.count())  # fmt: skip
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    # Ya existen y no cambian: por defecto se omiten
    assert [(p.existente.codigo, p.cambia, p.estado) for p in vista.partidas] == [
        ("P-001", False, "no se importa"), ("P-002", False, "no se importa"),
        ("P-003", False, "no se importa"), ("P-004", False, "no se importa"),
    ]  # fmt: skip
    assert not vista.a_importar and not vista.errores
    with pytest.raises(ValidationError, match="todas se omiten"):
        _importar(plantilla, gerente)
    # Aunque se elija actualizarlas, sin cambios no se toca nada (ni el historial)
    actualizar = {i: {"accion": "actualizar"} for i in range(4)}
    assert [p.estado for p in servicios.preparar_plantilla(
        plantilla, FICHERO, ajustes=actualizar).partidas] == ["sin cambios"] * 4  # fmt: skip
    resumen = _importar(plantilla, gerente, ajustes=actualizar)
    assert len(resumen.sin_cambios) == 4 and not resumen.creadas and not resumen.actualizadas
    despues = (Partida.objects.count(), Recurso.objects.count(),
               LineaDescompuesto.objects.count(), Partida.history.count(),
               LineaDescompuesto.history.count())  # fmt: skip
    assert antes == despues


def test_hoja_cambiada_actualiza_su_partida(plantilla, empresa, gerente):
    _importar(plantilla, gerente)
    pintura = _propia("P-001")
    cambiadas = [("PINTURA INTERIOR", 100, 4.2, [("Pintura plástica blanca", 3.0, 0, 0.3)], 0, 0,
                  500), *PARTIDAS[1:]]  # fmt: skip
    nueva = crear_plantilla(partidas=cambiadas)
    # Con cambios no se sobrescribe sin preguntar: falta elegir y no deja importar
    vista = servicios.preparar_plantilla(nueva, FICHERO)
    assert [p.estado for p in vista.partidas] == ["falta elegir"] + ["no se importa"] * 3
    assert not vista.se_puede_importar
    assert vista.errores == [
        "PINTURA INTERIOR: ya existe en el banco (P-001) y ha cambiado; elige "
        "si se actualiza, se crea otra con código nuevo o se omite."
    ]
    with pytest.raises(ValidationError):
        _importar(nueva, gerente)
    actualizar = {0: {"accion": "actualizar"}}
    vista = servicios.preparar_plantilla(nueva, FICHERO, ajustes=actualizar)
    assert vista.partidas[0].estado == "se actualiza" and vista.se_puede_importar
    _importar(nueva, gerente, ajustes=actualizar)
    pintura.refresh_from_db()
    assert Partida.objects.filter(resumen="PINTURA INTERIOR").count() == 1
    assert _lineas(pintura) == [("M-001", D("0.3000"), D("10.00")),
                                ("MO-OPERARIO", D("0.1778"), D("0.00"))]  # fmt: skip
    assert pintura.precio_mo_referencia == D("4.2000")
    assert pintura.history.first().history_change_reason.startswith("Importado de la plantilla")


def _pintura_cambiada():
    cambiadas = [("PINTURA INTERIOR", 100, 4.2, [("Pintura plástica blanca", 3.0, 0, 0.3)], 0, 0,
                  500), *PARTIDAS[1:]]  # fmt: skip
    return crear_plantilla(partidas=cambiadas)


def test_hoja_cambiada_crear_otra_con_codigo_nuevo(plantilla, empresa, gerente):
    _importar(plantilla, gerente)
    nueva = _pintura_cambiada()
    # El código que traía (el de la existente) se cambia por uno nuevo
    ajustes = {0: {"accion": "crear", "codigo": "P-001"}}
    vista = servicios.preparar_plantilla(nueva, FICHERO, ajustes=ajustes)
    assert (vista.partidas[0].codigo, vista.partidas[0].estado) == ("P-005", "nueva")
    resumen = _importar(nueva, gerente, ajustes=ajustes)
    assert [p.codigo for p in resumen.creadas] == ["P-005"] and not resumen.actualizadas
    assert _propia("P-001").precio_mo_referencia == D("2.1000")  # la de antes, intacta
    assert _propia("P-005").precio_mo_referencia == D("4.2000")
    # Las dos tienen el mismo nombre: la vista previa lo dice
    vista = servicios.preparar_plantilla(nueva, FICHERO)
    assert (vista.partidas[0].existente.codigo, vista.partidas[0].otras) == ("P-001", 1)


def test_hoja_cambiada_omitida(plantilla, empresa, gerente):
    _importar(plantilla, gerente)
    resumen = _importar(
        _pintura_cambiada(), gerente, ajustes={0: {"accion": "omitir"}, 1: {"accion": "actualizar"}}
    )
    assert not resumen.creadas and not resumen.actualizadas and len(resumen.sin_cambios) == 1
    assert _propia("P-001").precio_mo_referencia == D("2.1000")


def test_partida_del_banco_con_el_mismo_nombre_ya_existe(plantilla, empresa, gerente):
    propia = servicios.guardar_partida(
        Partida(banco=servicios.banco_propio(), codigo="PI", unidad="m2",
                resumen="Pintura  interior"), [], [], gerente,
    )  # fmt: skip
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    primera = vista.partidas[0]
    assert (primera.existente, primera.estado, primera.codigo) == (propia, "falta elegir", "PI")
    assert [p.estado for p in vista.partidas[1:]] == ["nueva"] * 3
    _importar(plantilla, gerente, ajustes={0: {"accion": "actualizar", "unidad": "m²"}})
    propia.refresh_from_db()
    assert (propia.unidad, propia.precio_mo_referencia, propia.lineas.count()) == (
        "m²",
        D("2.1000"),
        2,
    )


def test_nombres_iguales_sin_tildes_ni_mayusculas_son_el_mismo_material(plantilla, empresa,
                                                                         gerente):  # fmt: skip
    _importar(plantilla, gerente)
    pizarra = _propia(resumen="EFECTO PIZARRA")
    codigos = [c for c, _, _ in _lineas(pizarra)]
    assert "M-001" in codigos  # «pintura  plástica BLANCA» es «Pintura plástica blanca»
    assert Recurso.objects.filter(resumen__iexact="pintura plástica blanca").count() == 1


def test_leccion_5_erratas_propuestas_para_unir(plantilla, empresa, gerente):
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    assert [u.nombres for u in vista.uniones] == [("Ejecto pizarra", "Efecto pizarra")]
    assert [nombre for _, nombre in vista.uniones[0].opciones] == [
        "Ejecto pizarra",
        "Efecto pizarra",
    ]
    assert any(lec.numero == 5 for lec in vista.lecciones)
    # Sin confirmar, son dos materiales
    _importar(plantilla, gerente)
    assert Recurso.objects.filter(resumen__in=["Ejecto pizarra", "Efecto pizarra"]).count() == 2


def test_leccion_5_union_confirmada(plantilla, empresa, gerente):
    union = servicios.preparar_plantilla(plantilla, FICHERO).uniones[0]
    efecto = next(valor for valor, nombre in union.opciones if nombre == "Efecto pizarra")
    vista = servicios.preparar_plantilla(plantilla, FICHERO, uniones=[efecto])
    assert vista.uniones[0].elegida == efecto
    _importar(plantilla, gerente, uniones=[efecto])
    assert not Recurso.objects.filter(resumen="Ejecto pizarra").exists()
    efecto = Recurso.objects.get(resumen="Efecto pizarra")
    assert _propia(resumen="EFECTO PIZARRA").lineas.filter(recurso=efecto).exists()
    assert _propia(resumen="BARNIZ MADERA").lineas.filter(recurso=efecto).exists()


def test_errata_frente_a_un_material_del_banco_solo_deja_el_del_banco(plantilla, empresa,
                                                                      gerente):  # fmt: skip
    propio = servicios.banco_propio()
    servicios.guardar_recurso(
        Recurso(banco=propio, codigo="ESM", tipo=Recurso.MATERIAL, resumen="Esmalte brilo",
                precio=D("10")), gerente)  # fmt: skip
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    union = next(u for u in vista.uniones if "Esmalte brillo" in u.nombres)
    assert [nombre for _, nombre in union.opciones] == ["Esmalte brilo"]
    _importar(plantilla, gerente, uniones=[union.opciones[0][0]])
    assert "ESM" in [c for c, _, _ in _lineas(_propia("P-002"))]


def test_material_que_ya_esta_en_el_banco_se_reutiliza(plantilla, empresa, gerente):
    propio = servicios.banco_propio()
    existente = servicios.guardar_recurso(
        Recurso(banco=propio, codigo="PINT-B", tipo=Recurso.MATERIAL, unidad="l",
                resumen="Pintura plastica blanca", precio=D("2.80")), gerente)  # fmt: skip
    vista = servicios.preparar_plantilla(plantilla, FICHERO)
    assert any("ya está en el banco (PINT-B) con otro precio" in a for a in vista.avisos)
    _importar(plantilla, gerente)
    assert _lineas(_propia("P-001"))[0][0] == "PINT-B"
    existente.refresh_from_db()
    assert existente.precio == D("2.8000")  # se mantiene el del banco


def test_ajustes_de_la_vista_previa(plantilla, empresa, gerente):
    ajustes = {0: {"importar": True, "codigo": "PINT-INT", "unidad": "m2"},
               3: {"importar": False}}  # fmt: skip
    resumen = _importar(plantilla, gerente, ajustes=ajustes)
    assert len(resumen.creadas) == 3
    assert _propia("PINT-INT").unidad == "m2"
    assert not Partida.objects.filter(resumen="BARNIZ MADERA").exists()


def test_codigo_repetido_o_ya_usado_no_deja_importar(plantilla, empresa, gerente):
    servicios.guardar_partida(Partida(banco=servicios.banco_propio(), codigo="OCUPADO",
                                      resumen="Otra"), [], [], gerente)  # fmt: skip
    ajustes = {0: {"importar": True, "codigo": "X"}, 1: {"importar": True, "codigo": "X"},
               2: {"importar": True, "codigo": "OCUPADO"}}  # fmt: skip
    vista = servicios.preparar_plantilla(plantilla, FICHERO, ajustes=ajustes)
    assert not vista.se_puede_importar
    assert any("se repite" in e for e in vista.errores)
    assert any("ya es de otra partida" in e for e in vista.errores)
    with pytest.raises(ValidationError):
        _importar(plantilla, gerente, ajustes=ajustes)
    assert Partida.objects.count() == 1


def test_seccion_en_las_nuevas_e_historial_de_alta(plantilla, empresa, gerente):
    pt = Seccion.objects.create(codigo="PT", nombre="Pintura")
    _importar(plantilla, gerente, seccion=pt)
    pintura = _propia("P-001")
    assert pintura.seccion == pt
    registros = list(pintura.history.all())
    assert len(registros) == 1 and registros[0].history_type == "+"
    assert registros[0].history_change_reason == f"Importado de la plantilla {FICHERO}"


def test_fichero_sin_partidas(empresa, gerente):
    vista = servicios.preparar_plantilla(b"no es excel", "x.xlsx")
    assert not vista.se_puede_importar and vista.errores
    with pytest.raises(ValidationError):
        servicios.importar_plantilla(b"no es excel", "x.xlsx", gerente)
