"""Empresa (modelo único) y secciones."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import ModeloAuditado
from core.validadores import validar_color_hex, validar_nif_cif

PORCENTAJE = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))]


def campo_pct(verbose, defecto):
    return models.DecimalField(
        verbose,
        max_digits=5,
        decimal_places=2,
        default=Decimal(defecto),
        validators=PORCENTAJE,
        help_text="En %: 12,50 significa 12,5 %.",
    )


class Empresa(ModeloAuditado):
    """Una sola empresa por instalación. Siempre tiene pk=1."""

    PK_UNICA = 1

    # Identificación
    razon_social = models.CharField("razón social", max_length=200)
    nombre_comercial = models.CharField(max_length=200, blank=True)
    cif = models.CharField("CIF", max_length=12, validators=[validar_nif_cif])
    direccion = models.CharField("dirección", max_length=255, blank=True)
    cp = models.CharField("código postal", max_length=5, blank=True)
    poblacion = models.CharField("población", max_length=100, blank=True)
    provincia = models.CharField(max_length=100, blank=True)
    telefono = models.CharField("teléfono", max_length=20, blank=True)
    email = models.EmailField(blank=True)
    web = models.URLField(blank=True)

    # Imagen y documentos
    logo = models.ImageField(upload_to="empresa/", blank=True)
    color_primario = models.CharField(
        max_length=7, default="#1F6FEB", validators=[validar_color_hex]
    )
    iban = models.CharField("IBAN", max_length=34, blank=True)
    pie_legal = models.TextField(blank=True, help_text="Registro mercantil, protección de datos…")

    # Anchos de los códigos analíticos de cliente y obra: se eligen al instalar y quedan fijos
    ancho_cliente = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(3), MaxValueValidator(6)]
    )
    ancho_obra = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(4), MaxValueValidator(6)]
    )

    # Parámetros económicos de la empresa: desvío, jornada, recargos y umbrales de aviso
    pct_desvio_defecto = campo_pct("% desvío por defecto", "2.00")
    pct_desvio_tope = campo_pct("% desvío máximo", "4.00")
    jornada_horas = models.DecimalField(
        "jornada (h)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("8.00"),
        validators=[MinValueValidator(Decimal("1")), MaxValueValidator(Decimal("12"))],
    )
    recargo_festivos = campo_pct("recargo sábados y festivos (%)", "50.00")
    dias_aviso_pago = models.PositiveSmallIntegerField(
        "plazo máximo de pago para aviso (días)", default=60
    )
    umbral_aviso_coste_hora = campo_pct(
        "aviso de desviación del coste hora frente al último mes cerrado (%)", "15.00"
    )

    history = HistoricalRecords()
    parte_permisos = "ajustes"  # quién ve su historial (core/historial.py)

    class Meta:
        verbose_name = "empresa"
        verbose_name_plural = "empresa"
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name="empresa_unica"),
        ]

    def __str__(self):
        return self.nombre_comercial or self.razon_social

    def clean(self):
        errores = {}
        if self.pct_desvio_defecto is not None and self.pct_desvio_tope is not None:
            if self.pct_desvio_defecto > self.pct_desvio_tope:
                errores["pct_desvio_defecto"] = "El desvío por defecto no puede superar el máximo."
        if self.cif:
            self.cif = self.cif.replace(" ", "").replace("-", "").upper()
        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        if self._state.adding and type(self).objects.filter(pk=self.PK_UNICA).exists():
            raise ValidationError("Ya existe una empresa: solo puede haber una por instalación.")
        self.pk = self.PK_UNICA
        super().save(*args, **kwargs)

    @classmethod
    def actual(cls):
        """La empresa configurada, o None si aún no se ha dado de alta."""
        return cls.objects.filter(pk=cls.PK_UNICA).first()


class Seccion(ModeloAuditado):
    """Sección o departamento. Las define cada empresa según sus oficios, p. ej.
    ALB (albañilería), INS (instalaciones), CAR (carpintería), PYL (pladur), PT (pintura)."""

    codigo = models.CharField(
        "código",
        max_length=4,
        unique=True,
        validators=[RegexValidator(r"^[A-Z0-9]{1,4}$", "Hasta 4 letras MAYÚSCULAS o números.")],
    )
    nombre = models.CharField(max_length=100)
    activa = models.BooleanField(default=True)
    pct_beneficio_defecto = campo_pct("% beneficio objetivo por defecto", "15.00")
    plantilla_fases = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Fases de avance para certificar una partida por partes, opcionales; "
            'p. ej. [{"nombre": "Replanteo", "pct": 10}, …]. Los % deben sumar 100. '
            "Vacía si la sección no usa fases."
        ),
    )

    history = HistoricalRecords()
    parte_permisos = "ajustes"
    # Solo con «ver márgenes»: oculto en listas, formularios e historial
    campos_margen = ("pct_beneficio_defecto",)
    baja_si = {"activa": False}  # archivar cuenta como baja en el historial

    class Meta:
        verbose_name = "sección"
        verbose_name_plural = "secciones"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"

    def save(self, *args, **kwargs):
        # El código es inmutable una vez creada la sección: los documentos se pasan de una
        # sección a otra, no se renombra la sección.
        if not self._state.adding:
            guardadas = type(self).objects.filter(pk=self.pk)
            original = guardadas.values_list("codigo", flat=True).first()
            if original is not None and original != self.codigo:
                raise ValidationError(
                    {"codigo": "El código de una sección no se puede cambiar una vez creada."}
                )
        super().save(*args, **kwargs)

    def clean(self):
        if not self.plantilla_fases:
            return
        if not isinstance(self.plantilla_fases, list):
            raise ValidationError({"plantilla_fases": "Debe ser una lista de fases."})
        total = Decimal("0")
        for fase in self.plantilla_fases:
            if not isinstance(fase, dict) or not fase.get("nombre") or "pct" not in fase:
                raise ValidationError({"plantilla_fases": "Cada fase necesita 'nombre' y 'pct'."})
            total += Decimal(str(fase["pct"]))
        if total != Decimal("100"):
            raise ValidationError(
                {"plantilla_fases": f"Los % de las fases deben sumar 100 (suman {total})."}
            )
