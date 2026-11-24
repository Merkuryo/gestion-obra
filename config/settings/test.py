from .base import *  # noqa: F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Modelos de prueba para los componentes comunes (selector de sección)
INSTALLED_APPS = [*INSTALLED_APPS, "core.tests.app_pruebas"]  # noqa: F405
