"""Textos para mensajes. Sin Django: lo usan también los lectores de ficheros."""


def contar(n: int, singular: str, plural: str) -> str:
    """«1 partida nueva», «2 partidas nuevas», «0 partidas nuevas»."""
    return f"{n} {singular if n == 1 else plural}"


def enumerar(partes) -> str:
    """«a», «a y b», «a, b y c»."""
    partes = list(partes)
    if len(partes) <= 1:
        return "".join(partes)
    return f"{', '.join(partes[:-1])} y {partes[-1]}"
