from django import forms

from core.formularios import CampoMayusculas, ColorInput, FormularioBootstrap
from core.validadores import validar_logo

from .models import Empresa, Seccion

GRUPOS = [
    (
        "Identificación",
        [
            "razon_social",
            "nombre_comercial",
            "cif",
            "direccion",
            "cp",
            "poblacion",
            "provincia",
            "telefono",
            "email",
            "web",
        ],
    ),
    ("Imagen y documentos", ["logo", "color_primario", "iban", "pie_legal"]),
    ("Códigos analíticos", ["ancho_cliente", "ancho_obra"]),
    (
        "Parámetros económicos",
        [
            "pct_desvio_defecto",
            "pct_desvio_tope",
            "jornada_horas",
            "recargo_festivos",
            "dias_aviso_pago",
            "umbral_aviso_coste_hora",
        ],
    ),
]


class EmpresaForm(FormularioBootstrap, forms.ModelForm):
    # Sin esquema (www.demo.es) se interpreta como https
    web = forms.URLField(required=False, assume_scheme="https")

    grupos = GRUPOS

    class Meta:
        model = Empresa
        fields = [campo for _, campos in GRUPOS for campo in campos]
        widgets = {
            "logo": forms.FileInput(attrs={"accept": ".png,.jpg,.jpeg,image/png,image/jpeg"}),
            "color_primario": ColorInput(),
            "pie_legal": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["logo"].validators.append(validar_logo)
        self.fields["logo"].help_text = "PNG o JPG, máximo 2 MB. No se admite SVG."
        # Los anchos de código se eligen al instalar y quedan fijos: cambiarlos rompería los códigos.
        # Un campo desactivado ignora lo que llegue en el POST y conserva el valor guardado.
        if self.instance.pk:
            for nombre in ("ancho_cliente", "ancho_obra"):
                self.fields[nombre].disabled = True
                self.fields[nombre].help_text = "Fijo tras la instalación."


class SeccionForm(FormularioBootstrap, forms.ModelForm):
    # Se escribe en mayúsculas (si llega en minúsculas se convierte). Inmutable tras crearla.
    codigo = CampoMayusculas(
        label="Código",
        max_length=4,
        help_text="Hasta 4 letras MAYÚSCULAS o números. No se puede cambiar después.",
        widget=forms.TextInput(attrs={"class": "text-uppercase", "autocomplete": "off"}),
    )

    # Las fases por manos (plantilla_fases) aún no tienen pantalla propia: se editan en el admin.
    class Meta:
        model = Seccion
        fields = ["codigo", "nombre", "pct_beneficio_defecto", "activa"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:  # un campo desactivado ignora el POST y conserva el valor guardado
            self.fields["codigo"].disabled = True
