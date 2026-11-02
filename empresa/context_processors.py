from .models import Empresa

COLOR_POR_DEFECTO = "#1F6FEB"


def _hex_a_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def tema_desde_color(color: str | None) -> dict:
    """Variables CSS de Bootstrap 5.3 derivadas del color corporativo de la empresa."""
    color = color or COLOR_POR_DEFECTO
    r, g, b = _hex_a_rgb(color)
    oscuro = "#{:02x}{:02x}{:02x}".format(*(int(c * 0.85) for c in (r, g, b)))
    return {"primario": color, "primario_rgb": f"{r}, {g}, {b}", "primario_oscuro": oscuro}


def empresa(request):
    """Pone la empresa y su tema de color a disposición de todas las plantillas."""
    actual = Empresa.actual()
    return {"empresa": actual, "tema": tema_desde_color(actual.color_primario if actual else None)}
