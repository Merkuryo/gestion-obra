from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from simple_history.models import HistoricalRecords


class Usuario(AbstractUser):
    """Usuario de la app. Los roles son grupos de Django (ver core/roles.py)."""

    telefono = models.CharField("teléfono", max_length=20, blank=True)
    cargo = models.CharField(
        max_length=100, blank=True, help_text="Aparece en la firma de las ofertas."
    )
    # Selector de sección de la barra superior (core/secciones.py). Vacío = todas las secciones.
    seccion_elegida = models.ForeignKey(
        "empresa.Seccion",
        verbose_name="sección de trabajo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Filtra las listas y es la sección por defecto al crear documentos.",
    )

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"


class ModeloAuditado(models.Model):
    """Campos comunes a todos los modelos: quién creó y modificó cada registro, y cuándo."""

    creado_en = models.DateTimeField(auto_now_add=True)
    modificado_en = models.DateTimeField(auto_now=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    modificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )

    class Meta:
        abstract = True


class Rol(ModeloAuditado):
    """Rol editable de la empresa. Va unido a un grupo de Django: los usuarios del grupo
    tienen el rol. Qué puede hacer en cada parte de la app está en PermisoRol.

    Los roles de serie (Gerencia, Administración, Oficina técnica, Jefe de obra y Encargado)
    son plantillas: se pueden editar y duplicar, pero no renombrar.
    """

    grupo = models.OneToOneField("auth.Group", on_delete=models.PROTECT, related_name="rol")
    nombre = models.CharField(max_length=150, unique=True)
    descripcion = models.CharField("descripción", max_length=255, blank=True)
    es_plantilla = models.BooleanField("plantilla de serie", default=False, editable=False)
    activo = models.BooleanField(default=True, help_text="Un rol inactivo no da ningún permiso.")
    ver_nominas = models.BooleanField(
        "ver nóminas individuales",
        default=False,
        help_text="Coste mensual de cada persona. El coste hora se ve sin este permiso.",
    )
    ver_margenes = models.BooleanField(
        "ver márgenes",
        default=False,
        help_text="Márgenes y beneficio de presupuestos y obras, dentro del ámbito del rol.",
    )

    history = HistoricalRecords()
    parte_permisos = "roles"  # quién ve su historial (core/historial.py)
    campos_internos = ("grupo",)  # el grupo de Django repite el nombre: no sale en el historial
    baja_si = {"activo": False}

    class Meta:
        verbose_name = "rol"
        verbose_name_plural = "roles"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def historial_relacionado(self):
        """Los cambios de permisos salen en el historial del rol, con la parte como título.
        Los usuarios que entran y salen quedan en el motivo de cada cambio del rol."""
        from .historial import Relacionado
        from .permisos import PARTES

        return [
            Relacionado(
                PermisoRol.history.filter(rol_id=self.pk),
                titulo=lambda r: f"Permisos · {PARTES.get(r.parte, r.parte)}",
                excluir=("rol", "parte"),
            )
        ]


class PermisoRol(ModeloAuditado):
    """Lo que un rol puede hacer en una parte de la app (las partes están en core/permisos.py)."""

    TODAS = "todas"
    ASIGNADAS = "asignadas"
    AMBITOS = [(TODAS, "Todas las obras"), (ASIGNADAS, "Solo las asignadas")]

    rol = models.ForeignKey(Rol, on_delete=models.CASCADE, related_name="permisos")
    parte = models.CharField(max_length=50)
    ver = models.BooleanField(default=False)
    crear = models.BooleanField(default=False)
    editar = models.BooleanField(default=False)
    aprobar = models.BooleanField(default=False)
    ver_importes = models.BooleanField(default=False)
    ambito = models.CharField("ámbito", max_length=10, choices=AMBITOS, default=TODAS)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "permiso de rol"
        verbose_name_plural = "permisos de rol"
        constraints = [
            models.UniqueConstraint(fields=["rol", "parte"], name="permiso_unico_por_parte"),
        ]

    def __str__(self):
        return f"{self.rol} · {self.parte}"

    def save(self, *args, **kwargs):
        # Cualquier otra acción exige poder ver la parte
        if self.crear or self.editar or self.aprobar or self.ver_importes:
            self.ver = True
        super().save(*args, **kwargs)
