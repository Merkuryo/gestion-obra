from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

MUESTRAS = Path(__file__).parent / "muestras"


def leer(*argumentos):
    salida = StringIO()
    call_command("leer_bc3", *argumentos, stdout=salida)
    return salida.getvalue()


@pytest.mark.django_db
def test_resumen_sin_guardar_nada(django_assert_num_queries):
    with django_assert_num_queries(0):
        texto = leer(str(MUESTRAS / "presupuesto_2016.bc3"))
    assert "Versión: FIEBDC-3/2016 · Programa: Programa de ejemplo 1.0" in texto
    assert "Juego de caracteres: ANSI (leído como cp1252)" in texto
    assert "Raíz: REF## — Reforma de baño de ejemplo" in texto
    assert (
        "Conceptos: 16 · Capítulos: 3 · Partidas: 3 · Descompuestos: 9 (15 líneas) · "
        "Mediciones: 3 · Textos: 2"
    ) in texto
    assert "Avisos (1):" in texto and "línea 33: Registros ~L ignorados" in texto
    assert texto.rstrip().endswith("No se ha guardado nada.")


def test_arbol():
    texto = leer(str(MUESTRAS / "presupuesto_2016.bc3"), "--arbol")
    assert "  01# Albañilería\n    01.1# Tabiques\n      ALB020 m2 Tabique" in texto
    assert "PIN010 m2 Pintura plástica lisa en paredes · 40 · 6.75" in texto


def test_con_errores_los_muestra_y_termina_con_error(capsys):
    salida = StringIO()
    with pytest.raises(CommandError, match="5 error"):
        call_command("leer_bc3", str(MUESTRAS / "mal_formado.bc3"), stdout=salida)
    texto = salida.getvalue()
    assert "Errores (5):" in texto and "línea 12: Descomposición circular" in texto
    assert "No se ha guardado nada." in texto


def test_fichero_que_no_existe(tmp_path):
    salida = StringIO()
    with pytest.raises(CommandError):
        call_command("leer_bc3", str(tmp_path / "no.bc3"), stdout=salida)
    assert "No se puede leer el fichero" in salida.getvalue()


def test_limita_los_avisos_salvo_con_todas(tmp_path):
    lineas = ["~V||FIEBDC-3/2016|||ANSI||"] + [f"~X{i}|otro|" for i in range(60)]
    fichero = tmp_path / "muchos.bc3"
    fichero.write_bytes("\r\n".join(lineas).encode("cp1252"))
    texto = leer(str(fichero))
    # 60 registros ignorados y uno por no tener raíz
    assert "Avisos (61):" in texto and "… y 11 más (usa --todas)." in texto
    assert "más (usa --todas)" not in leer(str(fichero), "--todas")
