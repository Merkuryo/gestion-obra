"""Plantilla de cálculo de muestra con datos inventados, con el mismo formato de celdas que la
plantilla real y sus defectos (los que la app debe detectar y corregir).

Se genera en el momento con openpyxl: las celdas de datos llevan valores y las que sirven para
detectar los defectos llevan la fórmula (openpyxl no calcula, así que no guarda su valor).
"""

import io

import openpyxl

from importadores.plantilla import FILA_TITULOS

PARTIDAS = [
    # nombre, medición, MO/ud, materiales (nombre, precio, dto, consumo), fijaciones,
    # transportes, total K47
    ("PINTURA INTERIOR", 100, 2.1, [("Pintura plástica blanca", 3.0, 0, 0.25)], 0, 0, 400),
    ("ESMALTE METAL", 20, 12.9,
     [("Imprimación antioxidante", 8.0, 0.1, 0.2), ("Esmalte brillo", 10.0, 0, 0.15)],
     0.5, 0.25, 600),
    ("EFECTO PIZARRA", 10, 8.5,
     [("Ejecto pizarra", 11.0, 0, 0.1), ("pintura  plástica BLANCA", 3.0, 0, 0.05)], 0, 0, 300),
]  # fmt: skip
BIBLIOTECA = [("BARNIZ MADERA", 50, 15, [("Efecto pizarra", 11.0, 0, 0.12)], 0, 0, 999)]


def _hoja_partida(libro, nombre, medicion, mo, materiales, fijaciones, transportes, total,
                  coste_dia_formula=True):  # fmt: skip
    h = libro.create_sheet(nombre)
    h["C6"], h["D6"], h["E6"], h["F6"] = "M2 Obra", medicion, "Pvp. M Obra", mo
    h["G6"], h["H6"] = "% Venta", 0.1
    h["G8"], h["H8"], h["F8"] = "% Obras", 0, 0
    if coste_dia_formula:
        h["C9"] = "=189/F6"
    else:
        h["C9"] = round(189 / mo, 4)
    titulos = {"B": "MATERIALES", "G": "PVP. T", "M": "CONSUMO", "N": "MERMA 10%"}
    for columna, texto in titulos.items():
        h[f"{columna}{FILA_TITULOS}"] = texto
    for fila, (material, precio, dto, consumo) in enumerate(materiales, start=14):
        h[f"B{fila}"], h[f"G{fila}"], h[f"I{fila}"] = material, precio, dto
        h[f"M{fila}"], h[f"N{fila}"] = consumo, round(consumo * 1.1, 6)
        h[f"A{fila}"] = f"=N{fila}"
    h["G36"], h["H36"] = "FIJACIONES", fijaciones
    h["G37"], h["H37"] = "TRANSPORTES", transportes
    h["G38"], h["H38"] = "MANO DE OBRA", "=+F6"
    h["G39"], h["H39"] = "GASTOS DE EMPRESA", "=ROUND(+(H34+H36+H37+H38)*(0.14),2)"
    h["J39"] = "=ROUND(+(J34+J36+J37+J38)*(0.14),2)"
    h["G41"], h["H41"], h["J41"] = "GASTO FINANCIERO SOBRE LOS MATERIALES", "=+H34*F8", "=+J34*H8"
    h["G47"], h["H47"], h["K47"] = "TOTAL VENTA M2", round(total / medicion, 2), total
    h["G53"], h["F53"] = "KILOS", "=A14*D6"
    h["G54"], h["F54"] = "SACOS", "=+A15*D6/(25)"
    return h


def crear_plantilla(partidas=PARTIDAS, biblioteca=BIBLIOTECA, coste_dia_formula=True) -> bytes:
    libro = openpyxl.Workbook()
    indice = libro.active
    indice.title = "ÍNDICE"
    for celda, valor in {"F6": 20, "F7": 0, "F8": 0.4, "F9": 2}.items():
        indice[celda] = valor
    libro.create_sheet("Hoja1")
    for datos in partidas:
        _hoja_partida(libro, *datos, coste_dia_formula=coste_dia_formula)
    libro.create_sheet("Hoja2")

    resumen = libro.create_sheet("RESUMEN")
    for fila, texto in {4: "MANO DE OBRA", 5: "MATERIALES", 8: "COSTE DIRECTO",
                        10: "Desplazamiento a obra", 13: "COSTE INDIRECTO"}.items():  # fmt: skip
        resumen[f"A{fila}"] = texto
    resumen["A15"], resumen["F15"] = "COSTE TOTAL", sum(p[-1] for p in partidas)

    rp = libro.create_sheet("RESUMEN PARTIDAS")
    for col, texto in zip("ABCDEFK", ["UD", "DESCIPCIÓN", "MATERIAL", "M.OBRA", "DESPLAZ",
                                      "C. DIRECTO", "TOTAL"], strict=True):  # fmt: skip
        rp[f"{col}5"] = texto
    # La macro dejó una fila duplicada
    filas = [(f"{partidas[0][0]} (2)", partidas[0][-1])] + [(p[0], p[-1]) for p in partidas]
    for fila, (nombre, total) in enumerate(filas, start=6):
        rp[f"B{fila}"], rp[f"K{fila}"] = nombre, total

    libro.create_sheet("MATERIAL")["A1"] = "MATERIAL"
    for datos in biblioteca:
        _hoja_partida(libro, *datos, coste_dia_formula=coste_dia_formula)
    libro.create_sheet("NOTAS")["A1"] = "Hoja de notas sin formato de partida"

    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
