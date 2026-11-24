"""Banco de precios: el banco propio de la empresa y los bancos externos importados de BC3.

- **Banco propio:** uno solo; sus partidas y recursos se editan y llevan historial y sección.
- **Bancos externos:** uno por cada BC3 importado, de solo lectura, con su origen y fecha.
  Reimportar el mismo banco lo actualiza por código (catalogos/servicios.importar_bc3).

Una partida se descompone en líneas: cada línea es un **recurso** (mano de obra, maquinaria,
material) o una **partida auxiliar** (que a su vez se descompone), con factor y rendimiento.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

from core.models import ModeloAuditado


def precio_d4(verbose="precio"):
    return models.DecimalField(verbose, max_digits=12, decimal_places=4, null=True, blank=True)


class Banco(ModeloAuditado):
    PROPIO, EXTERNO = "propio", "externo"
    TIPOS = [(PROPIO, "Propio"), (EXTERNO, "Externo (BC3)")]

    tipo = models.CharField(max_length=10, choices=TIPOS, default=EXTERNO)
    nombre = models.CharField(max_length=200)
    # Bancos externos: de dónde salen. `clave` identifica el mismo banco al reimportarlo.
    clave = models.CharField(max_length=255, blank=True)
    fichero = models.CharField("fichero de origen", max_length=255, blank=True)
    version_formato = models.CharField("versión del formato", max_length=30, blank=True)
    programa = models.CharField("programa que lo generó", max_length=200, blank=True)
    propietario = models.CharField(max_length=200, blank=True)
    fecha_fichero = models.DateField("fecha del fichero", null=True, blank=True)
    importado_en = models.DateTimeField(null=True, blank=True)
    importado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )  # fmt: skip

    history = HistoricalRecords()
    parte_permisos = "banco"

    class Meta:
        verbose_name = "banco de precios"
        verbose_name_plural = "bancos de precios"
        ordering = ["tipo", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo"], condition=Q(tipo="propio"), name="banco_propio_unico"
            ),
            models.UniqueConstraint(
                fields=["clave"], condition=Q(tipo="externo"), name="banco_externo_clave_unica"
            ),
        ]

    def __str__(self):
        return self.nombre

    @property
    def es_propio(self):
        return self.tipo == self.PROPIO


class Capitulo(ModeloAuditado):
    """Capítulo de un banco externo (para navegarlo como en su programa de origen)."""

    banco = models.ForeignKey(Banco, on_delete=models.CASCADE, related_name="capitulos")
    padre = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="hijos"
    )
    codigo = models.CharField("código", max_length=40)
    resumen = models.CharField(max_length=255, blank=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "capítulo"
        verbose_name_plural = "capítulos"
        ordering = ["banco", "orden"]
        constraints = [
            models.UniqueConstraint(fields=["banco", "codigo"], name="capitulo_codigo_por_banco")
        ]

    def __str__(self):
        return f"{self.codigo} {self.resumen}".strip()


class Recurso(ModeloAuditado):
    """Mano de obra, maquinaria o material: lo que entra en el descompuesto de una partida."""

    MANO_OBRA, MAQUINARIA, MATERIAL, OTRO = "mano_obra", "maquinaria", "material", "otro"
    TIPOS = [
        (MANO_OBRA, "Mano de obra"),
        (MAQUINARIA, "Maquinaria y medios auxiliares"),
        (MATERIAL, "Material"),
        (OTRO, "Otro"),
    ]
    # Lo que falta para que el precio sea el de la empresa (recursos copiados de un banco externo)
    PENDIENTE_COSTE_HORA, PENDIENTE_TARIFA = "coste_hora", "tarifa"
    PENDIENTES = [
        ("", "—"),
        (PENDIENTE_COSTE_HORA, "Recalcular con el coste hora"),
        (PENDIENTE_TARIFA, "Enlazar con un artículo y su tarifa"),
    ]

    banco = models.ForeignKey(Banco, on_delete=models.CASCADE, related_name="recursos")
    codigo = models.CharField("código", max_length=40)
    tipo = models.CharField(max_length=12, choices=TIPOS, default=MATERIAL)
    unidad = models.CharField(max_length=20, blank=True)
    resumen = models.CharField(max_length=255)
    texto = models.TextField("texto largo", blank=True)
    precio = precio_d4()
    fecha_precio = models.DateField("fecha del precio", null=True, blank=True)
    origen = models.CharField(
        max_length=255, blank=True, help_text="Banco y código de donde se copió."
    )
    pendiente = models.CharField(max_length=12, choices=PENDIENTES, blank=True)

    history = HistoricalRecords()
    parte_permisos = "banco"
    campos_importe = ("precio",)

    class Meta:
        verbose_name = "recurso"
        verbose_name_plural = "recursos"
        ordering = ["banco", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["banco", "codigo"], name="recurso_codigo_por_banco")
        ]

    def __str__(self):
        return f"{self.codigo} {self.resumen}"


class Partida(ModeloAuditado):
    """Partida del banco: unidad, resumen, texto largo y su descompuesto (`lineas`)."""

    banco = models.ForeignKey(Banco, on_delete=models.CASCADE, related_name="partidas")
    capitulo = models.ForeignKey(
        Capitulo, null=True, blank=True, on_delete=models.SET_NULL, related_name="partidas"
    )
    seccion = models.ForeignKey(
        "empresa.Seccion", verbose_name="sección", null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )  # fmt: skip
    codigo = models.CharField("código", max_length=40)
    unidad = models.CharField(max_length=20, blank=True)
    resumen = models.CharField(max_length=255)
    texto = models.TextField("texto largo", blank=True)
    precio = precio_d4()
    fecha_precio = models.DateField("fecha del precio", null=True, blank=True)
    auxiliar = models.BooleanField(
        default=False, help_text="Solo se usa dentro del descompuesto de otras partidas."
    )
    origen = models.CharField(
        max_length=255, blank=True, help_text="Banco y código de donde se copió."
    )

    history = HistoricalRecords()
    parte_permisos = "banco"
    campos_importe = ("precio",)

    class Meta:
        verbose_name = "partida"
        verbose_name_plural = "partidas"
        ordering = ["banco", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["banco", "codigo"], name="partida_codigo_por_banco")
        ]

    def __str__(self):
        return f"{self.codigo} {self.resumen}"

    def historial_relacionado(self):
        from core.historial import Relacionado

        def titulo(registro):
            elemento = registro.recurso or registro.auxiliar
            return f"Descompuesto · {elemento.codigo if elemento else 'línea'}"

        return [
            Relacionado(
                LineaDescompuesto.history.filter(partida_id=self.pk).select_related(
                    "recurso", "auxiliar"
                ),
                titulo,
                excluir=("partida", "orden"),
            )
        ]


class LineaDescompuesto(ModeloAuditado):
    """Una línea del descompuesto: un recurso o una partida auxiliar, por unidad de partida.

    Cantidad por unidad = factor × rendimiento × (1 + merma %).
    """

    partida = models.ForeignKey(Partida, on_delete=models.CASCADE, related_name="lineas")
    orden = models.PositiveIntegerField(default=0)
    recurso = models.ForeignKey(
        Recurso, null=True, blank=True, on_delete=models.PROTECT, related_name="lineas"
    )
    auxiliar = models.ForeignKey(
        Partida, verbose_name="partida auxiliar", null=True, blank=True,
        on_delete=models.PROTECT, related_name="usada_en",
    )  # fmt: skip
    factor = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0"))],
    )  # fmt: skip
    rendimiento = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    merma_pct = models.DecimalField(
        "merma (%)", max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )  # fmt: skip

    history = HistoricalRecords()
    parte_permisos = "banco"

    class Meta:
        verbose_name = "línea de descompuesto"
        verbose_name_plural = "líneas de descompuesto"
        ordering = ["partida", "orden", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(recurso__isnull=False, auxiliar__isnull=True)
                    | Q(recurso__isnull=True, auxiliar__isnull=False)
                ),
                name="linea_recurso_o_auxiliar",
            )
        ]

    def __str__(self):
        elemento = self.recurso or self.auxiliar
        return f"{elemento} × {self.rendimiento}"

    @property
    def elemento(self):
        return self.recurso or self.auxiliar
