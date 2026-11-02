from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Usuario(AbstractUser):
    """Usuario de la app. Los roles son grupos de Django (ver core/roles.py)."""

    telefono = models.CharField("teléfono", max_length=20, blank=True)
    cargo = models.CharField(
        max_length=100, blank=True, help_text="Aparece en la firma de las ofertas."
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
