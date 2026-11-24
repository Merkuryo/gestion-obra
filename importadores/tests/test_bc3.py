"""Lector BC3 con ficheros de muestra creados a mano (importadores/tests/muestras): pequeños,
con datos ficticios, uno por cada caso que importa. No se usa ninguna base con licencia."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from importadores.bc3 import AVISO, ERROR, leer_bc3

MUESTRAS = Path(__file__).parent / "muestras"
V = r"~V|EMPRESA DE EJEMPLO|FIEBDC-3/2016\01102026|Programa de ejemplo||ANSI||2|||"


def bc3(*lineas, cabecera=V, codificacion="cp1252"):
    """Bytes de un BC3 pequeño escrito en el propio test."""
    todas = ([cabecera] if cabecera else []) + list(lineas)
    return "\r\n".join(todas).encode(codificacion) + b"\r\n"


def mensajes(resultado, nivel=None):
    return [i.mensaje for i in resultado.incidencias if nivel is None or i.nivel == nivel]


def lineas_de(resultado, nivel):
    return [i.linea for i in resultado.incidencias if i.nivel == nivel]


@pytest.fixture(scope="module")
def presupuesto():
    return leer_bc3(MUESTRAS / "presupuesto_2016.bc3")


# ── Presupuesto completo (FIEBDC-3/2016, ANSI) ─────────────────────────────────


def test_cabecera_v(presupuesto):
    r = presupuesto
    assert r.version == "FIEBDC-3/2016" and r.fecha_version == date(2026, 11, 24)
    assert r.propietario == "EMPRESA DE EJEMPLO" and r.programa == "Programa de ejemplo 1.0"
    assert r.cabecera == "Reforma de ejemplo" and r.rotulos == ["Presupuesto"]
    assert r.juego_de_caracteres == "ANSI" and r.codificacion == "cp1252"
    assert r.comentario == "Fichero de prueba creado a mano"
    assert r.nombre_tipo_informacion == "Presupuesto"


def test_decimales_y_porcentajes_k(presupuesto):
    d = presupuesto.decimales
    assert d.campo_1 == {"DN": 2, "DD": 2, "DS": 2, "DR": 3, "DI": 2, "DP": 2, "DC": 2, "DM": 2}
    # 14 posiciones según la norma, dos de ellas reservadas (vacías): 12 decimales
    assert d.campo_3["DRC"] == 3 and d.campo_3["DUO"] == 2 and d.campo_3["DS"] == 2
    assert len(d.campo_3) == 12 and "" not in d.campo_3
    assert d.divisa == "EUR" and d.medicion_total == 2
    assert d.porcentajes == {
        "CI": Decimal("0"),
        "GG": Decimal("13"),
        "BI": Decimal("6"),
        "BAJA": Decimal("0"),
        "IVA": Decimal("21"),
    }


def test_conceptos_c(presupuesto):
    c = presupuesto.conceptos["ALB010"]
    assert (c.unidad, c.resumen) == ("m2", "Enlucido de yeso en paredes")
    assert c.precio == Decimal("12.40")
    assert c.fecha == date(2026, 10, 1) and c.tipo == "0"
    assert presupuesto.conceptos["MO2"].resumen == "Peón ordinario"  # tildes en cp1252
    tipos = {k: presupuesto.conceptos[k].nombre_tipo for k in ("MO1", "MQ1", "MT1")}
    assert tipos == {"MO1": "Mano de obra", "MQ1": "Maquinaria y medios auxiliares",
                     "MT1": "Materiales"}  # fmt: skip
    assert presupuesto.conceptos["REF##"].es_raiz
    assert presupuesto.conceptos["01#"].es_capitulo
    assert not presupuesto.conceptos["REF##"].es_capitulo
    assert presupuesto.conceptos["01#"].precio is None  # precio vacío


def test_textos_largos_t(presupuesto):
    texto = presupuesto.conceptos["ALB010"].texto
    # El texto puede ocupar varias líneas; los caracteres especiales de cp1252 se leen bien
    assert texto == (
        "Enlucido de yeso a buena vista sobre paramentos verticales,\n"
        "incluso guardavivos. Precio orientativo: 12,40 €/m²."
    )
    assert presupuesto.conceptos["PIN010"].texto.startswith("Dos manos")
    assert presupuesto.conceptos["MO1"].texto == ""


def test_arbol_capitulos_partidas_descompuestos(presupuesto):
    r = presupuesto
    assert r.raiz.codigo == "REF##" and r.raiz.clase == "raiz"
    assert [n.codigo for n in r.raiz.hijos] == ["01#", "02#"]
    assert [n.codigo for n in r.capitulos] == ["01#", "01.1#", "02#"]
    # Subcapítulo dentro de capítulo, y partida al lado del subcapítulo
    alb = r.capitulos[0]
    hijos = [(n.codigo, n.clase) for n in alb.hijos]
    assert hijos == [("01.1#", "capitulo"), ("ALB010", "partida")]
    assert [n.codigo for n in r.partidas] == ["ALB020", "ALB010", "PIN010"]
    assert [n.padre.codigo for n in r.partidas] == ["01.1#", "01#", "02#"]
    assert {n.clase for n in r.lineas_descompuestas} == {"descompuesto"}


def test_descomposicion_d_con_factor_rendimiento_y_varios_niveles(presupuesto):
    alb010 = next(n for n in presupuesto.partidas if n.codigo == "ALB010")
    assert [(h.codigo, h.factor, h.rendimiento) for h in alb010.hijos] == [
        ("MO1", Decimal("1"), Decimal("0.25")),
        ("MT1", Decimal("1"), Decimal("1.05")),
        ("AX1", Decimal("1"), Decimal("0.01")),
    ]
    # El auxiliar AX1 tiene su propia descomposición: tercer nivel bajo la partida
    auxiliar = alb010.hijos[2]
    assert [(h.codigo, h.rendimiento, h.nivel) for h in auxiliar.hijos] == [
        ("MO2", Decimal("1.5"), 4),
        ("MT4", Decimal("0.25"), 4),
        ("MQ1", Decimal("0.5"), 4),
    ]
    assert (alb010.nivel, auxiliar.nivel) == (2, 3)  # raíz 0 · 01# 1 · ALB010 2 · AX1 3
    pin = next(n for n in presupuesto.partidas if n.codigo == "PIN010")
    assert pin.hijos[1].factor == Decimal("1.1")  # factor distinto de 1


def test_mediciones_m(presupuesto):
    r = presupuesto
    assert len(r.mediciones) == 3
    alb010 = next(n for n in r.partidas if n.codigo == "ALB010")
    m = alb010.medicion
    assert (m.padre, m.codigo, m.posicion) == ("01#", "ALB010", ["1", "2"])
    assert m.total == Decimal("25.5") and m.total_calculado == Decimal("25.50")
    assert [(ln.comentario, ln.parcial) for ln in m.lineas] == [
        ("Salón", Decimal("11.25")),
        ("Dormitorio", Decimal("15.0")),
        ("Hueco de puerta", Decimal("-0.75")),  # deducción
    ]
    # La línea de subtotal no suma; las dimensiones vacías no cuentan
    alb020 = next(n for n in r.partidas if n.codigo == "ALB020")
    tipos = [(ln.tipo, ln.parcial) for ln in alb020.medicion.lineas]
    assert tipos == [("", Decimal("7.5")), ("1", None), ("", Decimal("5.25"))]
    assert alb020.medicion.total_calculado == Decimal("12.75")


def test_recuento_y_un_solo_aviso(presupuesto):
    assert presupuesto.recuento() == {
        "conceptos": 16,
        "capitulos": 3,
        "partidas": 3,
        "descompuestos": 9,
        "lineas_descompuestas": 15,
        "mediciones": 3,
        "textos": 2,
        "avisos": 1,
        "errores": 0,
    }
    assert presupuesto.es_valido
    [aviso] = presupuesto.avisos
    assert aviso.linea == 33 and "~L ignorados (1)" in aviso.mensaje


# ── Versiones y juegos de caracteres ───────────────────────────────────────────


@pytest.mark.parametrize(
    "fichero, version, juego, codificacion",
    [
        ("banco_2007_850.bc3", "FIEBDC-3/2007", "850", "cp850"),
        ("banco_2012_437.bc3", "FIEBDC-3/2012", "437", "cp437"),
        ("banco_2020_sin_juego.bc3", "FIEBDC-3/2020", "", "cp1252"),
    ],
)
def test_versiones_y_juegos_de_caracteres(fichero, version, juego, codificacion):
    r = leer_bc3(MUESTRAS / fichero)
    assert (r.version, r.juego_de_caracteres, r.codificacion) == (version, juego, codificacion)
    assert r.es_valido and r.avisos == []
    assert r.nombre_tipo_informacion == "Base de datos"
    grifo = r.conceptos["FON010"]
    assert grifo.resumen == "Grifo de baño monomando"
    assert r.conceptos["OF1"].resumen == "Oficial fontanero, peón y ayudante"
    assert r.conceptos["GR1"].resumen == "Grifo monomando cromado, señalizado"
    assert grifo.texto.startswith("Suministro y colocación")
    # Sinónimos y varios precios: vale el primero
    assert grifo.sinonimos == ["FON-010"]
    assert grifo.precios == [Decimal("45.30"), Decimal("48.00")]
    assert grifo.precio == Decimal("45.30")


def test_el_juego_de_v_decide_como_se_leen_las_tildes():
    datos = (MUESTRAS / "banco_2007_850.bc3").read_bytes().replace(b"|850|", b"|ANSI|")
    r = leer_bc3(datos)
    assert r.codificacion == "cp1252"
    assert r.conceptos["FON010"].resumen != "Grifo de baño monomando"


def test_bytes_que_no_existen_en_cp1252():
    r = leer_bc3(bc3("~C|A|ud|Concepto|1|||") + b"~C|B|ud|Mal \x81 escrito|1|||\r\n")
    [aviso] = [a for a in r.avisos if "cp1252" in a.mensaje]
    assert aviso.linea == 3
    assert r.conceptos["B"].resumen == "Mal � escrito"


def test_juego_desconocido_se_lee_como_ansi():
    r = leer_bc3(bc3("~C|A|ud|Peón|1|||", cabecera=r"~V||FIEBDC-3/2016|||UTF-9||"))
    assert r.codificacion == "cp1252" and r.conceptos["A"].resumen == "Peón"
    assert any("UTF-9" in m for m in mensajes(r, AVISO))


@pytest.mark.parametrize("version", ["FIEBDC-3/2002", "FIEBDC-3/98", "OTRO"])
def test_versiones_no_admitidas_avisan_y_se_leen(version):
    r = leer_bc3(bc3("~C|A|ud|Concepto|1|||", cabecera=f"~V||{version}|||ANSI||"))
    assert r.es_valido and "A" in r.conceptos
    assert any("fuera de las admitidas" in m for m in mensajes(r, AVISO))


def test_sin_cabecera_v():
    r = leer_bc3(bc3("~C|A|ud|Concepto|1|||", cabecera=None))
    assert r.codificacion == "cp1252" and "A" in r.conceptos
    assert any("Falta la cabecera ~V" in m for m in mensajes(r, AVISO))


# ── Decimales de ~K ────────────────────────────────────────────────────────────

MEDICION = (
    r"~C|R##||Raíz||||",
    r"~D|R##|C#\1\1\|",
    r"~C|C#||Capítulo||||",
    r"~D|C#|P\1\1.234\|",
    r"~C|P|m2|Partida|10|||",
    r"~M|C#\P||1.2|\Línea\1\1.234\\\|",
)


def test_el_total_de_la_medicion_se_redondea_con_los_decimales_de_k():
    # DS = 1 decimal: las líneas suman 1,234 → 1,2, igual que el total del fichero
    con_uno = leer_bc3(bc3(r"~K|2\2\1\3\2\2\2\2\EUR\||", *MEDICION))
    m = con_uno.mediciones[0]
    assert con_uno.decimales.medicion_total == 1
    assert m.total_calculado == Decimal("1.2") and con_uno.avisos == []

    # DS = 3 decimales: 1,234 ≠ 1,2 → aviso, y se respeta el total del fichero
    con_tres = leer_bc3(bc3(r"~K|2\2\3\3\2\2\2\2\EUR\||", *MEDICION))
    m = con_tres.mediciones[0]
    assert con_tres.decimales.medicion_total == 3 and m.total_calculado == Decimal("1.234")
    assert m.total == Decimal("1.2")
    assert any("dice 1.2 y sus líneas suman 1.234" in a for a in mensajes(con_tres, AVISO))


def test_la_suma_redondeada_que_cuadra_no_da_aviso():
    """59,278 redondeado a 2 decimales es 59,28: cuadra con el total del fichero."""
    lineas = (*MEDICION[:-1], r"~M|C#\P||59.28|\A\1\12.4\\2.7\\B\1\10.8\\2.7\\C\-2\0.82\\2.05\|")
    sin_k = leer_bc3(bc3(*lineas))
    assert sin_k.mediciones[0].total_calculado == Decimal("59.28") and sin_k.avisos == []
    con_k = leer_bc3(bc3(r"~K|2\2\2\3\2\2\2\2\EUR\||", *lineas))
    assert con_k.avisos == []


K_NORMA = r"~K|\2\2\3\3\2\2\2\EUR\|13\6\0\21|3\2\\2\2\\2\2\2\3\3\2\2\2\EUR\|"


def test_campo_3_de_k_segun_la_norma():
    """DRC\\DC\\\\DFS\\DRS\\\\DUO\\DI\\DES\\DN\\DD\\DS\\DSP\\DEC\\DIVISA: las posiciones 3 y 6 van
    vacías. Así lo escriben los programas de presupuestos (p. ej. la muestra de reforma)."""
    d = leer_bc3(bc3(K_NORMA)).decimales
    assert d.campo_3 == {
        "DRC": 3, "DC": 2, "DFS": 2, "DRS": 2, "DUO": 2, "DI": 2, "DES": 2,
        "DN": 3, "DD": 3, "DS": 2, "DSP": 2, "DEC": 2,
    }  # fmt: skip
    assert d.divisa == "EUR" and d.medicion_total == 2


def test_el_ds_del_campo_3_manda_sobre_el_del_campo_1():
    # Campo 1 con DS = 1 y campo 3 con DS = 3: vale el 3 (la norma actual)
    k = r"~K|2\2\1\3\2\2\2\2\EUR\||3\2\\3\3\\2\2\2\2\2\3\2\2\EUR\|"
    r = leer_bc3(bc3(k, *MEDICION))
    assert r.decimales.campo_1["DS"] == 1 and r.decimales.campo_3["DS"] == 3
    assert r.decimales.medicion_total == 3
    assert r.mediciones[0].total_calculado == Decimal("1.234")
    assert any("suman 1.234" in a for a in mensajes(r, AVISO))


def test_sin_campo_3_vale_el_campo_1():
    r = leer_bc3(bc3(r"~K|2\2\1\3\2\2\2\2\EUR\||", *MEDICION))
    assert r.decimales.campo_3 == {} and r.decimales.medicion_total == 1


def test_posicion_reservada_con_valor_da_aviso():
    # Orden antiguo sin las dos posiciones vacías: lo que cae en una reservada se avisa
    r = leer_bc3(bc3(r"~K|||3\2\3\3\3\3\2\2\2\2\2\2\2\EUR\|"))
    reservadas = [a for a in r.avisos if "reservada" in a.mensaje]
    assert [a.linea for a in reservadas] == [2, 2]
    assert "posición 3" in reservadas[0].mensaje and "posición 6" in reservadas[1].mensaje


def test_presupuesto_de_ejemplo_sin_avisos_de_medicion():
    r = leer_bc3(MUESTRAS / "presupuesto_ejemplo_reforma.bc3")
    assert r.es_valido
    assert r.recuento()["partidas"] == 8 and r.recuento()["mediciones"] == 8
    assert not [a for a in mensajes(r, AVISO) if "medición" in a.lower()]
    alb01 = next(n for n in r.partidas if n.codigo == "ALB01")
    assert alb01.medicion.total == Decimal("59.28")
    assert alb01.medicion.total_calculado == Decimal("59.28")  # las líneas suman 59,278


def test_sin_k_se_usan_dos_decimales():
    r = leer_bc3(bc3(*MEDICION))
    assert r.decimales.medicion_total == 2 and r.mediciones[0].total_calculado == Decimal("1.23")


def test_k_con_numeros_no_validos():
    r = leer_bc3(bc3(r"~K|2\x\1\||"))
    assert r.decimales.campo_1 == {"DN": 2, "DS": 1}
    assert lineas_de(r, ERROR) == [2]


# ── Casos sueltos de cada registro ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto, esperada",
    [("24112026", date(2026, 11, 24)), ("241126", date(2026, 11, 24)),
     ("241185", date(1985, 11, 24)), ("1126", date(2026, 11, 1))],
)  # fmt: skip
def test_formatos_de_fecha(texto, esperada):
    assert leer_bc3(bc3(f"~C|A|ud|Concepto|1|{texto}||")).conceptos["A"].fecha == esperada


def test_coma_decimal_se_lee_con_aviso():
    r = leer_bc3(bc3("~C|A|ud|Concepto|12,5|||"))
    assert r.conceptos["A"].precio == Decimal("12.5") and r.es_valido
    assert any("coma decimal" in m for m in mensajes(r, AVISO))


def test_factor_y_rendimiento_vacios_valen_uno():
    r = leer_bc3(bc3(r"~D|P|A\\\B\2\\|", "~C|P|ud|Partida|1|||", "~C|A|ud|A|1|||",
                     "~C|B|ud|B|1|||"))  # fmt: skip
    assert [(c.codigo, c.factor, c.rendimiento) for c in r.descomposiciones["P"]] == [
        ("A", Decimal("1"), Decimal("1")),
        ("B", Decimal("2"), Decimal("1")),
    ]


def test_descomposicion_con_codigos_de_porcentaje():
    r = leer_bc3(bc3(r"~D|P||A\1\2\%MA;%CI\|", "~C|P|ud|Partida|1|||", "~C|A|ud|A|1|||"))
    [c] = r.descomposiciones["P"]
    assert (c.codigo, c.rendimiento, c.porcentajes) == ("A", Decimal("2"), ["%MA", "%CI"])


def test_descomposicion_incompleta():
    r = leer_bc3(bc3(r"~D|P|A\1\2\B\1|", "~C|P|ud|Partida|1|||", "~C|A|ud|A|1|||",
                     "~C|B|ud|B|1|||"))  # fmt: skip
    assert [c.codigo for c in r.descomposiciones["P"]] == ["A", "B"]
    assert any("incompleta" in m for m in mensajes(r, AVISO))


def test_registros_repetidos_vale_el_ultimo():
    lineas = ["~C|A|ud|Primero|1|||", "~T|A|Texto uno|", "~C|A|ud|Segundo|2|||",
              "~T|A|Texto dos|", r"~D|A|B\1\1\|", r"~D|A|B\1\3\|", "~C|B|ud|B|1|||"]  # fmt: skip
    r = leer_bc3(bc3(*lineas))
    a = r.conceptos["A"]
    assert (a.resumen, a.precio, a.texto) == ("Segundo", Decimal("2"), "Texto dos")
    assert r.descomposiciones["A"][0].rendimiento == Decimal("3")
    assert lineas_de(r, AVISO) == [4, 5, 7, None]  # None: no hay raíz


def test_registro_partido_en_varias_lineas():
    r = leer_bc3(bc3("~C|A|ud|Concepto partido|", "1.5|01102026|3|"))
    assert r.conceptos["A"].precio == Decimal("1.5") and r.conceptos["A"].tipo == "3"


def test_medicion_con_formula_toma_el_total_del_fichero():
    r = leer_bc3(bc3(*MEDICION[:-1], r"~M|C#\P||7|3\a*b\2\3.5\\\|"))
    m = r.mediciones[0]
    assert m.total == Decimal("7") and m.total_calculado is None
    assert any("fórmula" in a for a in mensajes(r, AVISO))


def test_medicion_sin_total_usa_la_suma_de_lineas():
    r = leer_bc3(bc3(*MEDICION[:-1], r"~M|C#\P|||\A\2\1.5\\\|"))
    assert r.mediciones[0].total == Decimal("3.00")


def test_medicion_sin_padre_va_a_su_partida():
    r = leer_bc3(bc3(*MEDICION[:-1], r"~M|P||1.234||"))
    assert r.partidas[0].medicion.total == Decimal("1.234") and r.avisos == []


def test_codigos_de_capitulo_con_o_sin_almohadilla_en_d():
    r = leer_bc3(bc3(r"~C|R##||Raíz||||", r"~D|R##|C\1\1\|", "~C|C#||Capítulo||||",
                     r"~D|C|P\1\2\|", "~C|P|ud|Partida|1|||"))  # fmt: skip
    assert [n.codigo for n in r.capitulos] == ["C#"]
    assert [n.codigo for n in r.partidas] == ["P"] and r.es_valido


def test_sin_raiz_se_parte_de_los_capitulos_sueltos():
    r = leer_bc3(bc3("~C|C1#||Uno||||", r"~D|C1#|P\1\2\|", "~C|P|ud|Partida|1|||"))
    assert r.raiz is None and [n.codigo for n in r.capitulos] == ["C1#"]
    assert [n.codigo for n in r.partidas] == ["P"]
    assert any("No hay concepto raíz" in m for m in mensajes(r, AVISO))


def test_varias_raices():
    r = leer_bc3(bc3("~C|R1##||Una||||", "~C|R2##||Otra||||"))
    assert r.raiz.codigo == "R1##"
    assert any("varias raíces" in m for m in mensajes(r, AVISO))


# ── Ficheros mal formados ──────────────────────────────────────────────────────


def test_fichero_mal_formado_da_errores_claros_con_su_linea():
    r = leer_bc3(MUESTRAS / "mal_formado.bc3")
    assert not r.es_valido
    errores = [(e.linea, e.mensaje) for e in r.errores]
    assert errores == [
        (3, "Concepto ~C sin código: se ignora."),
        (4, "Precio de P1: «12,5x» no es un número."),
        (8, "Rendimiento de P1 en C1#: «abc» no es un número."),
        (12, "Descomposición circular: P2 se contiene a sí mismo (a través de A1)."),
        (15, "Registro mal formado (sin campos separados por «|»): «~C sin barras»."),
    ]
    avisos = mensajes(r, AVISO)
    for esperado in [
        "Texto antes del primer registro",
        "Versión «FIEBDC-3/2002» fuera de las admitidas",
        "fecha «01132026» no válida",
        "Tipo «9» de P1 desconocido",
        "P9, en la descomposición de C1#, no está definido",
        "La medición de P9 dice 10 y sus líneas suman 6.00",
        "Registros ~Q ignorados (1)",
        "Texto ~T de NOEXISTE",
    ]:
        assert any(esperado in a for a in avisos), esperado
    # Lo que sí se pudo leer sigue ahí
    assert [n.codigo for n in r.partidas] == ["P2"] and r.conceptos["P1"].resumen
    # Ordenados por línea
    lineas = [i.linea for i in r.incidencias if i.linea]
    assert lineas == sorted(lineas)


@pytest.mark.parametrize(
    "datos, mensaje",
    [
        (b"", "vacío"),
        (b"   \r\n", "vacío"),
        (b"Esto es un texto cualquiera", "No es un fichero BC3"),
        (b"%PDF-1.7\x00\x01\x02\xff\xfe binario", "No es un fichero BC3"),
    ],
)
def test_ficheros_que_no_son_bc3(datos, mensaje):
    r = leer_bc3(datos)
    assert not r.es_valido and mensaje in r.errores[0].mensaje
    assert r.recuento()["conceptos"] == 0


def test_fichero_que_no_existe(tmp_path):
    r = leer_bc3(tmp_path / "no_existe.bc3")
    assert not r.es_valido and "No se puede leer el fichero" in r.errores[0].mensaje


def test_basura_binaria_con_virgulillas_no_rompe():
    datos = bytes(range(256)) * 20 + b"~C|A|ud|Concepto|1|||\r\n~~~|||\\\\\\"
    r = leer_bc3(datos)  # no lanza excepciones
    assert "A" in r.conceptos


def test_arbol_demasiado_profundo_no_rompe():
    """Una cadena de 3000 auxiliares no agota la pila: se corta con un error."""
    lineas = ["~C|R##||Raíz||||", r"~D|R##|A0\1\1\|"]
    for i in range(3000):
        lineas += [f"~C|A{i}|ud|Auxiliar {i}|1|||", f"~D|A{i}|A{i + 1}\\1\\1\\|"]
    r = leer_bc3(bc3(*lineas))
    assert any("pasa de 100 niveles" in e.mensaje for e in r.errores)
