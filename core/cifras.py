"""Cifras en pantalla. Sin Django."""

from decimal import ROUND_HALF_UP, Decimal

DECIMALES_PRECIO_UNITARIO = (2, 4)


def decimales_precio(valor, minimo=2, maximo=4) -> int:
    """Decimales con los que se enseña un precio unitario: los que tenga guardados, entre
    `minimo` y `maximo`. 0,0120 → 3 («0,012»); 2,7100 → 2; 23,6250 → 3; 1,23456 → 4."""
    if not isinstance(valor, Decimal) or not valor.is_finite():
        return minimo
    exponente = valor.normalize().as_tuple().exponent
    return max(minimo, min(maximo, -exponente))


def redondear_precio(valor, minimo=2, maximo=4):
    """El precio unitario con esos decimales (redondeado, no cortado)."""
    if not isinstance(valor, Decimal) or not valor.is_finite():
        return valor
    return valor.quantize(Decimal(1).scaleb(-decimales_precio(valor, minimo, maximo)),
                          ROUND_HALF_UP)  # fmt: skip
