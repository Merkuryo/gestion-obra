from datetime import date
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from catalogos import servicios
from catalogos.models import Banco, Capitulo, LineaDescompuesto, Partida, Recurso

pytestmark = pytest.mark.django_db


def _codigos(modelo, banco, **filtro):
    return sorted(modelo.objects.filter(banco=banco, **filtro).values_list("codigo", flat=True))


def test_vista_previa_sin_guardar_nada(presupuesto_bc3):
    vista = servicios.preparar_bc3(presupuesto_bc3, "presupuesto_2016.bc3")
    assert vista.recuento == {
        "capitulos": 3, "partidas": 3, "auxiliares": 1, "recursos": 8, "lineas": 12,
    }  # fmt: skip
    assert vista.nombre == "Reforma de baño de ejemplo" and vista.existente is None
    assert any("~L ignorados" in a for a in vista.avisos) and vista.errores == []
    assert not Banco.objects.filter(tipo=Banco.EXTERNO).exists()


def test_importa_capitulos_partidas_recursos_y_descompuesto(presupuesto_bc3, gerente):
    resumen = servicios.importar_bc3(presupuesto_bc3, "presupuesto_2016.bc3", gerente)
    banco = resumen.banco
    assert resumen.nuevo and banco.tipo == Banco.EXTERNO
    assert (banco.version_formato, banco.programa) == ("FIEBDC-3/2016", "Programa de ejemplo 1.0")
    assert banco.fecha_fichero == date(2026, 11, 24) and banco.importado_por == gerente

    assert _codigos(Capitulo, banco) == ["01#", "01.1#", "02#"]
    assert Capitulo.objects.get(banco=banco, codigo="01.1#").padre.codigo == "01#"
    assert _codigos(Partida, banco, auxiliar=False) == ["ALB010", "ALB020", "PIN010"]
    assert _codigos(Partida, banco, auxiliar=True) == ["AX1"]
    assert _codigos(Recurso, banco) == ["MO1", "MO2", "MO3", "MQ1", "MT1", "MT2", "MT3", "MT4"]

    alb010 = Partida.objects.get(banco=banco, codigo="ALB010")
    assert alb010.capitulo.codigo == "01#" and alb010.precio == D("12.4000")
    assert alb010.texto.startswith("Enlucido de yeso a buena vista")
    assert [(ln.elemento.codigo, ln.rendimiento) for ln in alb010.lineas.all()] == [
        ("MO1", D("0.2500")), ("MT1", D("1.0500")), ("AX1", D("0.0100")),
    ]  # fmt: skip
    pin = Partida.objects.get(banco=banco, codigo="PIN010")
    assert pin.lineas.get(recurso__codigo="MT3").factor == D("1.1000")
    tipos = dict(Recurso.objects.filter(banco=banco).values_list("codigo", "tipo"))
    assert (tipos["MO1"], tipos["MQ1"], tipos["MT1"]) == ("mano_obra", "maquinaria", "material")
    # Los bancos externos no llevan sección ni historial de importación por fila
    assert not Partida.objects.filter(banco=banco, seccion__isnull=False).exists()


def test_reimportar_el_mismo_banco_no_duplica(presupuesto_bc3, gerente):
    primero = servicios.importar_bc3(presupuesto_bc3, "presupuesto_2016.bc3", gerente)
    pks = set(Partida.objects.filter(banco=primero.banco).values_list("pk", flat=True))
    vista = servicios.preparar_bc3(presupuesto_bc3, "otro_nombre.bc3")
    assert vista.existente == primero.banco  # se reconoce por la raíz y el propietario

    segundo = servicios.importar_bc3(presupuesto_bc3, "otro_nombre.bc3", gerente)
    assert not segundo.nuevo and segundo.banco == primero.banco
    assert Banco.objects.filter(tipo=Banco.EXTERNO).count() == 1
    assert set(Partida.objects.filter(banco=primero.banco).values_list("pk", flat=True)) == pks
    assert segundo.creados == {"capitulos": 0, "recursos": 0, "partidas": 0}
    assert segundo.actualizados == {"capitulos": 0, "recursos": 0, "partidas": 0}
    assert LineaDescompuesto.objects.filter(partida__banco=primero.banco).count() == 12


def test_reimportar_un_banco_cambiado_actualiza_y_quita_lo_que_ya_no_viene(
    presupuesto_bc3, gerente
):
    banco = servicios.importar_bc3(presupuesto_bc3, "v1.bc3", gerente).banco
    texto = presupuesto_bc3.decode("cp1252")
    antes, despues = "~C|MO1|h|Oficial de primera|22.50|", "~C|MO1|h|Oficial de primera|24.00|"
    texto = texto.replace(antes, despues)
    texto = texto.replace("~D|02#|PIN010\\1\\40\\|", "~D|02#||")
    for quitar in ("~C|PIN010|", "~D|PIN010|", "~T|PIN010|", "~M|02#\\PIN010|",
                   "~C|MO3|", "~C|MT3|"):  # fmt: skip
        texto = "\r\n".join(ln for ln in texto.split("\r\n") if not ln.startswith(quitar))

    resumen = servicios.importar_bc3(texto.encode("cp1252"), "v2.bc3", gerente)
    assert resumen.banco == banco and not resumen.nuevo
    assert resumen.actualizados["recursos"] == 1 and resumen.borrados["recursos"] == 2
    assert resumen.borrados["partidas"] == 1
    assert Recurso.objects.get(banco=banco, codigo="MO1").precio == D("24.0000")
    assert not Partida.objects.filter(banco=banco, codigo="PIN010").exists()
    banco.refresh_from_db()
    assert banco.fichero == "v2.bc3"


def test_otro_banco_es_otro_banco_externo(presupuesto_bc3, banco_850_bc3, gerente):
    servicios.importar_bc3(presupuesto_bc3, "a.bc3", gerente)
    otro = servicios.importar_bc3(banco_850_bc3, "b.bc3", gerente)
    assert otro.nuevo and Banco.objects.filter(tipo=Banco.EXTERNO).count() == 2
    grifo = Partida.objects.get(banco=otro.banco, codigo="FON010")
    assert grifo.resumen == "Grifo de baño monomando"  # leído en su juego de caracteres


def test_rendimientos_con_mas_de_cuatro_decimales(gerente):
    datos = (
        "~V||FIEBDC-3/2016|||ANSI||1|||\r\n~C|B##||Banco||||\r\n~D|B##|C#\\1\\1\\|\r\n"
        "~C|C#||Capítulo||||\r\n~D|C#|P\\1\\1\\|\r\n~C|P|m2|Partida|1|||\r\n"
        "~D|P|R\\1\\0.00125\\|\r\n~C|R|h|Recurso|10||1|\r\n"
    ).encode("cp1252")
    vista = servicios.preparar_bc3(datos, "r.bc3")
    assert any("más de 4 decimales" in a for a in vista.avisos)
    banco = servicios.importar_bc3(datos, "r.bc3", gerente).banco
    assert LineaDescompuesto.objects.get(partida__banco=banco).rendimiento == D("0.0013")


def test_fichero_sin_nada_que_importar(gerente):
    vista = servicios.preparar_bc3(b"esto no es un BC3", "x.bc3")
    assert not vista.se_puede_importar and vista.errores
    with pytest.raises(ValidationError):
        servicios.importar_bc3(b"esto no es un BC3", "x.bc3", gerente)


def _bc3_con_unidades(*unidades):
    conceptos = "".join(
        f"~C|P{n}|{u}|Partida {n}|1|||\r\n" for n, u in enumerate(unidades, start=1)
    )
    hijos = "".join(f"P{n}\\1\\1\\" for n in range(1, len(unidades) + 1))
    return (
        "~V||FIEBDC-3/2016|||ANSI||1|||\r\n~C|B##||Banco||||\r\n~D|B##|C#\\1\\1\\|\r\n"
        f"~C|C#||Capítulo||||\r\n~D|C#|{hijos}|\r\n{conceptos}"
    ).encode("cp1252")


def test_unidades_normalizadas_al_importar_y_aviso_de_las_desconocidas(gerente):
    datos = _bc3_con_unidades("M2", "m.l.", "u", "saco", "Saco")
    vista = servicios.preparar_bc3(datos, "u.bc3")
    aviso = "2 unidades no están en la lista de unidades y se guardan tal cual: «saco» y «Saco»."
    assert aviso in vista.avisos
    banco = servicios.importar_bc3(datos, "u.bc3", gerente).banco
    guardadas = Partida.objects.filter(banco=banco).order_by("codigo")
    assert [p.unidad for p in guardadas] == ["m²", "ml", "ud", "saco", "Saco"]
    una = servicios.preparar_bc3(_bc3_con_unidades("m2", "rollo"), "u.bc3")
    assert "1 unidad no está en la lista de unidades y se guarda tal cual: «rollo»." in una.avisos


def test_avisos_en_singular(gerente):
    datos = (
        "~V||FIEBDC-3/2016|||ANSI||1|||\r\n~C|B##||Banco||||\r\n~D|B##|C#\\1\\1\\|\r\n"
        "~C|C#||Capítulo||||\r\n~D|C#|P\\1\\1\\|\r\n~C|P|m2|Partida|1|||\r\n"
        "~D|P|R\\1\\0.00125\\|\r\n~C|R|h|Recurso|10||1|\r\n"
    ).encode("cp1252")
    vista = servicios.preparar_bc3(datos, "r.bc3")
    aviso = "1 precio o rendimiento con más de 4 decimales: se guarda redondeado a 4."
    assert aviso in vista.avisos
