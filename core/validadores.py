"""Validadores comunes."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
LETRAS_CONTROL_CIF = "JABCDEFGHI"

validar_color_hex = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Usa un color hexadecimal como #1F6FEB.")


def normalizar_documento(valor: str) -> str:
    return re.sub(r"[\s\-.]", "", valor or "").upper()


def es_nif_cif_valido(valor: str) -> bool:
    """True si `valor` es un DNI, NIE o CIF español con dígito de control correcto."""
    v = normalizar_documento(valor)

    if re.fullmatch(r"\d{8}[A-Z]", v):  # DNI
        return LETRAS_DNI[int(v[:8]) % 23] == v[8]

    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", v):  # NIE
        numero = str("XYZ".index(v[0])) + v[1:8]
        return LETRAS_DNI[int(numero) % 23] == v[8]

    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", v):  # CIF
        digitos = v[1:8]
        suma_pares = sum(int(digitos[i]) for i in (1, 3, 5))
        suma_impares = 0
        for i in (0, 2, 4, 6):
            doble = int(digitos[i]) * 2
            suma_impares += doble // 10 + doble % 10
        control = (10 - (suma_pares + suma_impares) % 10) % 10
        letra = LETRAS_CONTROL_CIF[control]
        if v[0] in "NPQRSW":
            return v[8] == letra
        if v[0] in "ABEH":
            return v[8] == str(control)
        return v[8] in (str(control), letra)

    return False


def validar_nif_cif(valor: str) -> None:
    if not es_nif_cif_valido(valor):
        raise ValidationError("CIF/NIF/NIE no válido: %(valor)s", params={"valor": valor})


LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_EXTENSIONES = (".png", ".jpg", ".jpeg")
LOGO_FORMATOS = ("PNG", "JPEG")


def validar_logo(archivo):
    """Logo de empresa: solo PNG o JPG, máximo 2 MB. El SVG se rechaza (puede llevar scripts).

    Se comprueba la extensión y el formato real de la imagen (Pillow), no solo el nombre.
    Se aplica a archivos recién subidos por formulario (ya pasados por forms.ImageField).
    """
    if archivo.size > LOGO_MAX_BYTES:
        raise ValidationError("El logo no puede pesar más de 2 MB.")
    if not archivo.name.lower().endswith(LOGO_EXTENSIONES):
        raise ValidationError("El logo debe ser un archivo PNG o JPG (no se admite SVG).")
    formato = getattr(getattr(archivo, "image", None), "format", None)
    if formato not in LOGO_FORMATOS:
        raise ValidationError("El logo debe ser una imagen PNG o JPG válida.")
