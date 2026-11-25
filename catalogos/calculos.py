"""Fórmulas puras del banco de precios (sin base de datos)."""

from decimal import ROUND_HALF_UP, Decimal

CIEN = Decimal("100")
D4 = Decimal("0.0001")


def cantidad_por_unidad(factor: Decimal, rendimiento: Decimal, merma_pct: Decimal) -> Decimal:
    """Cantidad de un recurso por unidad de partida: factor × rendimiento × (1 + merma %)."""
    return factor * rendimiento * (1 + merma_pct / CIEN)


def importe_linea(factor, rendimiento, merma_pct, precio) -> Decimal | None:
    """Importe de una línea por unidad de partida; None si el recurso no tiene precio."""
    if precio is None:
        return None
    return (cantidad_por_unidad(factor, rendimiento, merma_pct) * precio).quantize(
        D4, ROUND_HALF_UP
    )


def precio_partida(lineas) -> Decimal | None:
    """Precio por unidad = Σ importes de sus líneas, a 4 decimales.

    `lineas`: iterable de (factor, rendimiento, merma_pct, precio). Las líneas sin precio
    cuentan como 0. None si la partida no tiene líneas.
    """
    total, hay = Decimal("0"), False
    for factor, rendimiento, merma_pct, precio in lineas:
        hay = True
        importe = importe_linea(factor, rendimiento, merma_pct, precio)
        if importe is not None:
            total += importe
    return total.quantize(D4, ROUND_HALF_UP) if hay else None


def redondear_d4(valor: Decimal | None) -> tuple[Decimal | None, bool]:
    """(valor a 4 decimales, si se ha perdido precisión al redondear)."""
    if valor is None:
        return None, False
    redondeado = valor.quantize(D4, ROUND_HALF_UP)
    return redondeado, redondeado != valor


# ── Mano de obra: doble entrada precio por unidad ↔ rendimiento ────────────────
#   coste_día    = coste_hora × jornada
#   rendimiento  = coste_día / precio_MO_ud        (ud por día-operario)
#   precio_MO_ud = coste_día / rendimiento
#   horas_ud     = jornada / rendimiento           (lo que va al descompuesto, en h por unidad)


def coste_dia(coste_hora: Decimal, jornada: Decimal) -> Decimal:
    return (coste_hora * jornada).quantize(Decimal("0.01"), ROUND_HALF_UP)


def rendimiento_mo(coste_dia: Decimal, precio_mo_ud: Decimal) -> Decimal:
    """Unidades por día-operario a partir del precio de mano de obra por unidad."""
    return (coste_dia / precio_mo_ud).quantize(D4, ROUND_HALF_UP)


def precio_mo_ud(coste_dia: Decimal, rendimiento: Decimal) -> Decimal:
    """Precio de mano de obra por unidad a partir del rendimiento (ud por día-operario)."""
    return (coste_dia / rendimiento).quantize(D4, ROUND_HALF_UP)


def horas_por_unidad(jornada: Decimal, coste_dia: Decimal, precio_mo_ud: Decimal) -> Decimal:
    """Horas de mano de obra por unidad = jornada / rendimiento, sin redondear el rendimiento
    antes (= jornada × precio_MO_ud / coste_día)."""
    return (jornada * precio_mo_ud / coste_dia).quantize(D4, ROUND_HALF_UP)


def coste_hora_de_dia(coste_dia: Decimal, jornada: Decimal) -> Decimal:
    return (coste_dia / jornada).quantize(D4, ROUND_HALF_UP)
