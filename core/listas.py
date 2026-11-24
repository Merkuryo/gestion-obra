"""Vista de lista: búsqueda, orden y totales calculados en Python (no en plantillas)."""

from dataclasses import dataclass, replace
from decimal import Decimal

from django.db.models import Q, Sum

from .permisos import OCULTO, campos_ocultos
from .secciones import filtra_por_seccion, filtrar_por_seccion


@dataclass
class Columna:
    campo: str  # campo del modelo: sirve para ordenar y, si no hay `valor`, para leer el dato
    titulo: str
    numerica: bool = False  # alineada a la derecha, cifras tabulares
    decimales: int = 2
    ordenable: bool = True
    total: bool = False  # suma de la columna (sobre todos los registros filtrados) en el pie
    tipo: str = "texto"  # "texto" | "badge" (el valor es una tupla (texto, color de Bootstrap))
    valor: object = None  # función opcional objeto -> valor a mostrar


def _valor(columna, objeto):
    if columna.valor:
        return columna.valor(objeto)
    return getattr(objeto, columna.campo)


def _filtrar(queryset, q, buscar_en):
    """Cada palabra de `q` debe aparecer en alguno de los campos de `buscar_en`."""
    for palabra in q.split():
        condicion = Q()
        for campo in buscar_en:
            condicion |= Q(**{f"{campo}__icontains": palabra})
        queryset = queryset.filter(condicion)
    return queryset


def _ordenar(queryset, orden, columnas):
    permitidas = {c.campo for c in columnas if c.ordenable}
    if orden and orden.lstrip("-") in permitidas:
        return queryset.order_by(orden, "pk"), orden
    return queryset, ""


def _url_orden(request, campo, orden_actual):
    parametros = request.GET.copy()
    parametros["orden"] = f"-{campo}" if orden_actual == campo else campo
    return "?" + parametros.urlencode()


def preparar_lista(request, queryset, columnas, buscar_en=(), url_fila=None, por_seccion=True):
    """Aplica `?q=` y `?orden=` y devuelve todo lo que necesita `componentes/_lista.html`.

    - Sección: si el modelo tiene campo `seccion`, se filtra por la sección elegida en el
      selector de la barra (core/secciones.py); con «Todas» no se filtra. `queryset` llega ya
      limitado por permisos y ámbito: el filtro solo lo reduce. `por_seccion=False` lo desactiva.
    - Orden: solo por columnas declaradas `ordenable`; cualquier otro valor se ignora.
    - Totales: `aggregate(Sum)` sobre el queryset filtrado completo, en Decimal.
    """
    # Lo que el usuario no puede ver (importes, márgenes, nóminas) no se muestra, ni se busca,
    # ni se ordena, ni se suma: core.permisos.campos_ocultos
    usuario = getattr(request, "user", None)
    filtra_seccion = por_seccion and filtra_por_seccion(queryset.model)
    seccion = None
    if filtra_seccion:
        queryset, seccion = filtrar_por_seccion(queryset, usuario)
    ocultas = set(campos_ocultos(usuario, queryset.model()))
    buscar_en = tuple(c for c in buscar_en if c.split("__")[0] not in ocultas)
    columnas_orden = [c for c in columnas if c.campo not in ocultas]

    q = request.GET.get("q", "").strip()
    if q and buscar_en:
        queryset = _filtrar(queryset, q, buscar_en)
    queryset, orden = _ordenar(queryset, request.GET.get("orden", ""), columnas_orden)

    cabeceras = []
    for c in columnas:
        flecha = None
        if c.campo == orden:
            flecha = "asc"
        elif f"-{c.campo}" == orden:
            flecha = "desc"
        cabeceras.append(
            {
                "titulo": c.titulo,
                "numerica": c.numerica,
                "url_orden": (
                    _url_orden(request, c.campo, orden)
                    if c.ordenable and c.campo not in ocultas
                    else None
                ),
                "flecha": flecha,
            }
        )

    objetos = list(queryset)
    filas = []
    for objeto in objetos:
        # Por fila, porque el ámbito (obras asignadas) puede ocultar en unas filas y en otras no
        ocultos = campos_ocultos(usuario, objeto)
        ocultas.update(ocultos)
        celdas = [
            {"valor": OCULTO, "numerica": False, "tipo": "oculto"}
            if c.campo in ocultos
            else {
                "valor": _valor(c, objeto),
                "numerica": c.numerica,
                "tipo": c.tipo,
                "formato": f"{c.decimales}g",
            }
            for c in columnas
        ]
        url = url_fila(objeto) if url_fila else None
        filas.append({"objeto": objeto, "celdas": celdas, "url": url})

    totales = None
    columnas = [replace(c, total=False) if c.campo in ocultas else c for c in columnas]
    if any(c.total for c in columnas):
        sumas = queryset.aggregate(
            **{f"t{i}": Sum(c.campo) for i, c in enumerate(columnas) if c.total}
        )
        totales = [
            {
                "valor": sumas[f"t{i}"] or Decimal("0"),
                "formato": f"{c.decimales}g",
                "numerica": c.numerica,
            }
            if c.total
            else None
            for i, c in enumerate(columnas)
        ]

    return {
        "q": q,
        "orden": orden,
        "cabeceras": cabeceras,
        "filas": filas,
        "totales": totales,
        "num_registros": len(filas),
        "con_busqueda": bool(buscar_en),
        "filtra_seccion": filtra_seccion,  # la lista se recarga al cambiar el selector
        "seccion": seccion,  # sección por la que está filtrada, o None
    }
