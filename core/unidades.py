"""Unidades de medida normalizadas.

Una sola lista para toda la app: al importar (BC3, plantillas, documentos del cliente), al
guardar a mano y al comparar la unidad de una línea del presupuesto con la del banco.
Se guarda y se muestra la forma canónica («m²», «ml», «ud»…). Lo que no se reconoce se
guarda tal cual (sin espacios de más) y quien la usa avisa.

Sin Django: lo usan también los lectores de ficheros.
"""

import unicodedata

# Forma canónica → variantes aceptadas, ya en minúsculas, sin tildes, sin puntos ni espacios
# y con ² y ³ como 2 y 3 (ver `_clave`): «M2», «m 2», «m²» y «m2.» son todas «m2».
EQUIVALENCIAS = {
    "m²": ("m2",),
    "m³": ("m3",),
    "ml": ("ml", "m"),  # «m» es metro lineal
    "ud": ("ud", "u", "uds", "un", "und", "unidad", "unidades"),
    "h": ("h", "hr", "hora", "horas"),
    "d": ("d", "dia", "dias"),
    "mes": ("mes", "meses"),
    "kg": ("kg", "kgs"),
    "t": ("t", "tn"),
    "l": ("l", "lt", "litro", "litros"),
    "km": ("km",),
    "pa": ("pa",),
    "%": ("%",),
    "€": ("€", "eur", "euro", "euros"),
}
CANONICAS = tuple(EQUIVALENCIAS)
_POR_CLAVE = {v: canonica for canonica, variantes in EQUIVALENCIAS.items() for v in variantes}


def _clave(texto: str) -> str:
    texto = (texto or "").replace("²", "2").replace("³", "3")
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return "".join(c for c in texto.casefold() if c not in " .\t")


def normalizar_unidad(texto: str) -> tuple[str, bool]:
    """(unidad que se guarda, reconocida). «M2» → («m²», True); «saco» → («saco», False)."""
    canonica = _POR_CLAVE.get(_clave(texto))
    if canonica is not None:
        return canonica, True
    return " ".join((texto or "").split()), False


def unidad(texto: str) -> str:
    """La forma que se guarda (canónica si se reconoce; si no, el texto limpio)."""
    return normalizar_unidad(texto)[0]


def reconocida(texto: str) -> bool:
    return normalizar_unidad(texto)[1]


def misma_unidad(a: str, b: str) -> bool:
    """¿Son la misma unidad? «m2» y «M²», sí; «m» y «ml», también; «m²» y «m³», no."""
    ua, ub = unidad(a), unidad(b)
    return ua.casefold() == ub.casefold()


def no_reconocidas(textos) -> list[str]:
    """Las unidades (ya limpias) que no están en la lista, sin repetir y en orden."""
    vistas = {}
    for texto in textos:
        valor, ok = normalizar_unidad(texto)
        if valor and not ok:
            vistas.setdefault(valor, None)
    return list(vistas)
