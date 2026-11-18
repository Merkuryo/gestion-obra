"""Datos de demostración, claramente ficticios, para enseñar la aplicación y hacer capturas.

Se puede lanzar las veces que haga falta: deja siempre el mismo resultado.
Sobrescribe los datos de la empresa, así que solo se ejecuta con DEBUG activado.
"""

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.roles import GERENCIA
from empresa.models import Empresa, Seccion
from empresa.servicios import (
    archivar_seccion,
    guardar_empresa,
    guardar_seccion,
    reactivar_seccion,
)

LOGO_DEMO = Path(settings.BASE_DIR) / "static" / "demo" / "logo-ce.png"
NOMBRE_LOGO = "empresa/demo-logo-ce.png"

EMPRESA = {
    "razon_social": "Construcciones Ejemplo SL",
    "nombre_comercial": "Construcciones Ejemplo",
    "cif": "B12345674",  # ficticio, con dígito de control válido
    "direccion": "Calle de Ejemplo, 1",
    "cp": "08001",
    "poblacion": "Barcelona",
    "provincia": "Barcelona",
    "telefono": "930000000",
    "email": "demo@example.com",
    "web": "https://example.com",
    "color_primario": "#C2410C",
}

# (código, nombre, % beneficio objetivo, fases de avance, activa)
SECCIONES = [
    ("ALB", "Albañilería", "15.00", [], True),
    (
        "INS",
        "Instalaciones",
        "18.00",
        [
            {"nombre": "Preinstalación", "pct": 40},
            {"nombre": "Instalación", "pct": 40},
            {"nombre": "Pruebas y puesta en marcha", "pct": 20},
        ],
        True,
    ),
    ("CAR", "Carpintería", "12.00", [], False),
    ("PYL", "Pladur", "20.00", [], True),
    (
        "PT",
        "Pintura",
        "22.00",
        [
            {"nombre": "Imprimación", "pct": 30},
            {"nombre": "Primera mano", "pct": 35},
            {"nombre": "Acabado", "pct": 35},
        ],
        True,
    ),
]

USUARIO = {"username": "demo", "first_name": "Usuario", "last_name": "Demo"}
CONTRASENA_DEMO = "demo-capturas-2026"  # solo para la demo local; nunca en producción


class Command(BaseCommand):
    help = "Carga datos ficticios de demostración (empresa, secciones y usuario demo)."

    def handle(self, *args, **opciones):
        if not settings.DEBUG:
            raise CommandError(
                "Los datos de demostración solo se cargan con DEBUG activado: "
                "sobrescriben la empresa y crean un usuario con contraseña conocida."
            )
        with transaction.atomic():
            usuario = self.usuario_demo()
            self.empresa_demo(usuario)
            self.secciones_demo(usuario)
        self.stdout.write(
            self.style.SUCCESS(
                f"Datos de demostración listos. Usuario: {USUARIO['username']} "
                f"/ {CONTRASENA_DEMO} (Gerencia)."
            )
        )

    def usuario_demo(self):
        Usuario = get_user_model()
        usuario, _ = Usuario.objects.get_or_create(username=USUARIO["username"])
        usuario.first_name = USUARIO["first_name"]
        usuario.last_name = USUARIO["last_name"]
        usuario.is_active = True
        usuario.set_password(CONTRASENA_DEMO)
        usuario.save()
        usuario.groups.set([Group.objects.get(name=GERENCIA)])
        return usuario

    def empresa_demo(self, usuario):
        empresa = Empresa.actual() or Empresa()
        for campo, valor in EMPRESA.items():
            setattr(empresa, campo, valor)
        if empresa.logo.name != NOMBRE_LOGO:
            anterior = empresa.logo.name
            almacen = empresa.logo.storage
            if almacen.exists(NOMBRE_LOGO):
                almacen.delete(NOMBRE_LOGO)
            with LOGO_DEMO.open("rb") as fichero:
                empresa.logo.save(Path(NOMBRE_LOGO).name, File(fichero), save=False)
            if anterior and anterior != empresa.logo.name and almacen.exists(anterior):
                almacen.delete(anterior)
        empresa.full_clean()
        guardar_empresa(empresa, usuario)

    def secciones_demo(self, usuario):
        for codigo, nombre, beneficio, fases, activa in SECCIONES:
            seccion = Seccion.objects.filter(codigo=codigo).first() or Seccion(codigo=codigo)
            seccion.nombre = nombre
            seccion.pct_beneficio_defecto = Decimal(beneficio)
            seccion.plantilla_fases = fases
            if seccion.pk is None:
                seccion.activa = True
            seccion.full_clean()
            guardar_seccion(seccion, usuario)
            if seccion.activa != activa:
                if activa:
                    reactivar_seccion(seccion, usuario)
                else:
                    archivar_seccion(seccion, usuario)
