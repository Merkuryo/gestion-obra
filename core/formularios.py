"""Base de formularios para los componentes de templates/componentes.

Pone las clases de Bootstrap a cada campo, agrupa los campos en pestañas y permite
mostrar la ficha en solo lectura. Uso:

    class SeccionForm(FormularioBootstrap, forms.ModelForm):
        grupos = [("General", ["codigo", "nombre"]), ("Económico", ["pct_beneficio_defecto"])]
"""

from django import forms


class FormularioBootstrap:
    # Lista de (título, [nombres de campo]). Con más de un grupo se muestran pestañas.
    # Los campos que no estén en ningún grupo van en uno final "Otros".
    grupos: list[tuple[str, list[str]]] = []

    def __init__(self, *args, solo_lectura=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.solo_lectura = solo_lectura
        for campo in self.fields.values():
            self._poner_clases(campo)
            if solo_lectura:
                campo.disabled = True

    @staticmethod
    def _poner_clases(campo):
        widget = campo.widget
        tipo = getattr(widget, "input_type", "")
        if tipo == "checkbox":
            clase = "form-check-input"
        elif tipo == "color":
            clase = "form-control form-control-color"
        elif tipo == "select" or widget.__class__.__name__ in ("Select", "SelectMultiple"):
            clase = "form-select"
        else:
            clase = "form-control"
        existente = widget.attrs.get("class", "")
        widget.attrs["class"] = f"{existente} {clase}".strip()

    def grupos_renderizados(self):
        """Grupos listos para la plantilla: título, campos y si alguno tiene errores."""
        grupos = [(titulo, list(nombres)) for titulo, nombres in self.grupos]
        usados = {n for _, nombres in grupos for n in nombres}
        resto = [n for n in self.fields if n not in usados]
        if resto or not grupos:
            grupos.append(("Otros" if self.grupos else "General", resto))
        resultado = []
        for indice, (titulo, nombres) in enumerate(grupos):
            campos = [self[n] for n in nombres if n in self.fields]
            if not campos:
                continue
            resultado.append(
                {
                    "id": f"grupo-{indice}",
                    "titulo": titulo,
                    "campos": campos,
                    "con_errores": any(c.errors for c in campos),
                }
            )
        # Se abre la primera pestaña con errores; si no hay errores, la primera.
        primero = resultado[0] if resultado else None
        activo = next((g for g in resultado if g["con_errores"]), primero)
        for g in resultado:
            g["activo"] = g is activo
        return resultado

    def campos_ocultos(self):
        return [c for c in self if c.is_hidden]


class ColorInput(forms.TextInput):
    """Selector de color del navegador (<input type="color">) para valores #RRGGBB."""

    input_type = "color"


class CampoMayusculas(forms.CharField):
    """Texto que se convierte a MAYÚSCULAS antes de validar (p. ej. códigos de sección)."""

    def to_python(self, value):
        valor = super().to_python(value)
        return valor.upper() if valor else valor
