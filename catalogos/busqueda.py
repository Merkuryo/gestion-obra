"""Buscador de partidas del banco propio y de los bancos externos.

Texto completo de PostgreSQL (configuración `es_sin_tildes`: español, sin tildes y por raíz)
sobre código, resumen, capítulo y texto largo, más trigramas sobre código, resumen y capítulo
para las erratas. Los campos `busqueda` y `texto_busqueda` los mantiene la base de datos
(migración 0006), con índices GIN.

Cada palabra de la búsqueda tiene que aparecer (por su raíz, sin tildes) o parecerse a una
palabra del código, el resumen o el capítulo (las que llevan cifras, como un código, se buscan
también por el principio del código, no por parecido). Se ordena por relevancia: primero el código
exacto, luego el texto completo (pesa más el resumen que el capítulo y que el texto largo) y
luego el parecido.
"""

import html
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramWordSimilarity
from django.db import connection, transaction
from django.db.models import Case, Count, F, FloatField, Q, Value, When, Window
from django.db.models.functions import Coalesce
from django.utils.safestring import mark_safe

from core import unidades
from core.permisos import puede
from core.secciones import seccion_elegida

from .models import Banco, Partida

CONFIGURACION = "es_sin_tildes"
UMBRAL_PARECIDO = 0.5  # pg_trgm: «pintra» → «pintura» 0,57; «esmlte» → «esmalte» 0,50
POR_PAGINA = 25
PROPIO = "propio"
TODAS = "todas"  # sección: sin filtrar


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con los espacios justos (como `texto_busqueda`)."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.casefold().split())


def palabras(q: str) -> list[str]:
    """Palabras de la búsqueda, sin signos sueltos y sin repetir."""
    vistas = {}
    for palabra in re.findall(r"[\w.\-/]+", q or ""):
        palabra = palabra.strip(".-/")
        if palabra:
            vistas.setdefault(palabra, None)
    return list(vistas)


def _vacias(lista):
    """Las palabras que el diccionario español descarta («de», «con», «para»…)."""
    if not lista:
        return set()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p FROM unnest(%s::text[]) AS p WHERE to_tsvector(%s, p) = ''::tsvector",
            [lista, CONFIGURACION],
        )
        return {fila[0] for fila in cursor.fetchall()}


@dataclass
class Filtros:
    q: str = ""
    bancos: list = field(default_factory=list)  # "propio" y pk de bancos externos; vacío = todos
    seccion: str = ""  # pk, TODAS, o "" = la sección de trabajo del usuario
    unidad: str = ""
    capitulo: str = ""
    precio_min: Decimal | None = None
    precio_max: Decimal | None = None
    auxiliares: bool = False
    pagina: int = 1
    # A igualdad de relevancia, primero las de la sección de trabajo (el selector busca en
    # todas las secciones, pero propone antes las de la suya)
    seccion_primero: bool = False

    @classmethod
    def de_peticion(cls, datos):
        def decimal(nombre):
            valor = (datos.get(nombre) or "").strip().replace(".", "").replace(",", ".")
            try:
                return Decimal(valor) if valor else None
            except ArithmeticError:
                return None

        try:
            pagina = max(1, int(datos.get("pagina") or 1))
        except ValueError:
            pagina = 1
        return cls(
            q=(datos.get("q") or "").strip()[:200],
            bancos=[b for b in datos.getlist("banco") if b == PROPIO or b.isdigit()],
            seccion=(datos.get("seccion") or "").strip(),
            unidad=(datos.get("unidad") or "").strip(),
            capitulo=(datos.get("capitulo") or "").strip()[:100],
            precio_min=decimal("precio_min"),
            precio_max=decimal("precio_max"),
            auxiliares=bool(datos.get("auxiliares")),
            pagina=pagina,
        )


@dataclass
class Resultado:
    filtros: Filtros
    partidas: list  # la página, con `relevancia`
    total: int
    terminos: list  # palabras buscadas, normalizadas (para resaltar)
    seccion: object  # la sección aplicada, o None
    ve_importes: bool
    ignoradas: list = field(default_factory=list)  # palabras vacías que no cuentan

    @property
    def paginas(self):
        return max(1, -(-self.total // POR_PAGINA))

    @property
    def hay_mas(self):
        return self.filtros.pagina < self.paginas


def _seccion(usuario, filtros):
    from empresa.models import Seccion

    if filtros.seccion == TODAS:
        return None
    if filtros.seccion.isdigit():
        return Seccion.objects.filter(pk=filtros.seccion).first()
    return seccion_elegida(usuario)


def consulta(usuario, filtros: Filtros):
    """(queryset con `relevancia`, términos, sección, palabras ignoradas). Sin evaluar: los
    trigramas necesitan el umbral de `buscar` (que es quien la evalúa)."""
    partidas = Partida.objects.all()
    if not filtros.auxiliares:
        partidas = partidas.filter(auxiliar=False)

    # Bancos: el propio, algunos externos o todos
    if filtros.bancos:
        condicion = Q(pk__in=[])
        if PROPIO in filtros.bancos:
            condicion |= Q(banco__tipo=Banco.PROPIO)
        externos = [int(b) for b in filtros.bancos if b != PROPIO]
        if externos:
            condicion |= Q(banco__tipo=Banco.EXTERNO, banco_id__in=externos)
        partidas = partidas.filter(condicion)

    # Sección: solo la llevan las partidas del banco propio; las externas no se filtran
    seccion = _seccion(usuario, filtros)
    if seccion is not None:
        partidas = partidas.filter(Q(banco__tipo=Banco.EXTERNO) | Q(seccion=seccion))

    if filtros.unidad:
        # Las unidades se guardan normalizadas (core/unidades.py): «m2», «M²» y «m²» son la misma
        partidas = partidas.filter(unidad__iexact=unidades.unidad(filtros.unidad))
    if filtros.capitulo:
        texto = filtros.capitulo
        partidas = partidas.filter(
            Q(capitulo__codigo__unaccent__icontains=texto)
            | Q(capitulo__resumen__unaccent__icontains=texto)
        )
    # El precio solo filtra a quien puede verlo (si no, se podría adivinar filtrando)
    if puede(usuario, "banco", "ver_importes"):
        if filtros.precio_min is not None:
            partidas = partidas.filter(precio__gte=filtros.precio_min)
        if filtros.precio_max is not None:
            partidas = partidas.filter(precio__lte=filtros.precio_max)

    lista = palabras(filtros.q)
    ignoradas = _vacias(lista)
    utiles = [p for p in lista if p not in ignoradas]
    terminos = [normalizar(p) for p in utiles]
    propio_primero = Case(When(banco__tipo=Banco.PROPIO, then=Value(0)), default=Value(1))
    desempate = [propio_primero]
    trabajo = seccion_elegida(usuario) if filtros.seccion_primero else None
    if trabajo is not None:
        desempate = [Case(When(seccion=trabajo, then=Value(0)), default=Value(1)), propio_primero]
    if not utiles:
        partidas = partidas.annotate(relevancia=Value(0.0)).order_by(
            *desempate, "banco__nombre", "codigo", "pk"
        )
        return partidas, [], seccion, sorted(ignoradas)

    for palabra, termino in zip(utiles, terminos, strict=True):
        condicion = Q(busqueda=SearchQuery(palabra, config=CONFIGURACION, search_type="plain"))
        if any(c.isdigit() for c in termino):
            # Parece un código: por su principio, no por parecido (P-001 no es P-002)
            condicion |= Q(codigo__istartswith=palabra)
        elif len(termino) >= 3:
            condicion |= Q(texto_busqueda__trigram_word_similar=termino)
        partidas = partidas.filter(condicion)

    alguna = SearchQuery(utiles[0], config=CONFIGURACION, search_type="plain")
    for palabra in utiles[1:]:
        alguna |= SearchQuery(palabra, config=CONFIGURACION, search_type="plain")
    q_normal = normalizar(filtros.q)
    partidas = partidas.annotate(
        relevancia=(
            Case(When(codigo__iexact=filtros.q.strip(), then=Value(10.0)), default=Value(0.0),
                 output_field=FloatField())
            + Coalesce(SearchRank(F("busqueda"), alguna), Value(0.0)) * 2
            + Coalesce(TrigramWordSimilarity(q_normal, "texto_busqueda"), Value(0.0))
        )
    ).order_by("-relevancia", *desempate, "codigo", "pk")  # fmt: skip
    return partidas, terminos, seccion, sorted(ignoradas)


def buscar(usuario, filtros: Filtros, por_pagina=POR_PAGINA) -> Resultado:
    """Busca y devuelve la página pedida, ya evaluada."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL pg_trgm.word_similarity_threshold = %s", [UMBRAL_PARECIDO])
        partidas, terminos, seccion, ignoradas = consulta(usuario, filtros)
        inicio = (filtros.pagina - 1) * por_pagina
        # Primero solo el orden y el total (una consulta estrecha, una sola pasada) y después
        # las partidas de la página
        orden = list(
            partidas.annotate(total=Window(Count("pk"))).values_list("pk", "relevancia", "total")[
                inicio : inicio + por_pagina
            ]
        )
        total = orden[0][2] if orden else (partidas.count() if inicio else 0)
        completas = Partida.objects.select_related("banco", "capitulo", "seccion").in_bulk(
            [pk for pk, _, _ in orden]
        )
        pagina = []
        for pk, relevancia, _ in orden:
            completas[pk].relevancia = relevancia
            pagina.append(completas[pk])
    return Resultado(filtros, pagina, total, terminos, seccion,
                     puede(usuario, "banco", "ver_importes"), ignoradas)  # fmt: skip


# ── Resaltado ──────────────────────────────────────────────────────────────────


def _coincide(palabra, terminos):
    import difflib

    normal = normalizar(palabra)
    if len(normal) < 2:
        return False
    for termino in terminos:
        corto = min(len(termino), len(normal), 5)
        if normal == termino or (corto >= 4 and normal[:corto] == termino[:corto]):
            return True
        if len(termino) >= 3 and difflib.SequenceMatcher(None, normal, termino).ratio() >= 0.75:
            return True
    return False


def resaltar(texto: str, terminos) -> str:
    """El texto escapado con <mark> en las palabras que coinciden con la búsqueda (sin tildes,
    por el principio de la palabra o por parecido, como la búsqueda)."""
    if not terminos:
        return html.escape(texto or "")
    trozos = re.split(r"(\w+)", texto or "")
    salida = []
    for trozo in trozos:
        if trozo and re.fullmatch(r"\w+", trozo) and _coincide(trozo, terminos):
            salida.append(f"<mark>{html.escape(trozo)}</mark>")
        else:
            salida.append(html.escape(trozo))
    return mark_safe("".join(salida))  # noqa: S308 - todo lo demás va escapado


def fragmento(texto: str, terminos, ancho=160) -> str:
    """El trozo del texto largo alrededor de la primera coincidencia, resaltado ("" si no
    coincide nada)."""
    if not texto or not terminos:
        return ""
    for encontrado in re.finditer(r"\w+", texto):
        if _coincide(encontrado.group(), terminos):
            inicio = max(0, encontrado.start() - ancho // 3)
            fin = min(len(texto), inicio + ancho)
            trozo = texto[inicio:fin]
            prefijo = "… " if inicio else ""
            sufijo = " …" if fin < len(texto) else ""
            return mark_safe(prefijo + resaltar(trozo, terminos) + sufijo)  # noqa: S308
    return ""
