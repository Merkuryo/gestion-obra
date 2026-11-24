from django.db import models

from core.models import ModeloAuditado


class DocumentoDePrueba(ModeloAuditado):
    """Un documento con sección, como lo serán presupuestos, valoradas o proformas."""

    nombre = models.CharField(max_length=100)
    seccion = models.ForeignKey("empresa.Seccion", on_delete=models.PROTECT, verbose_name="sección")
    asignado = models.BooleanField(default=False)  # para simular el ámbito «solo asignadas»

    parte_permisos = "ajustes"

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre
