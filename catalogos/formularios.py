from decimal import Decimal

from django import forms
from django.core.validators import FileExtensionValidator
from django.forms import BaseInlineFormSet, inlineformset_factory

from core import unidades
from core.formularios import FormularioBootstrap

from .models import LineaDescompuesto, Partida, Recurso

MAX_BC3_MB = 100
MAX_PLANTILLA_MB = 100


class PrecioInput(forms.NumberInput):
    """Precio con 2 decimales en pantalla; si se guardó con más (hasta 4), se ven para no
    perderlos al volver a guardar: 2.7100 → «2.71», 23.6250 → «23.625»."""

    def format_value(self, value):
        if isinstance(value, Decimal):
            exponente = min(value.normalize().as_tuple().exponent, -2)
            value = value.quantize(Decimal(1).scaleb(exponente))
        return super().format_value(value)


AYUDA_UNIDAD = (
    "Se guarda en su forma normalizada (m2 → m², M.L. → ml, u → ud…): "
    + ", ".join(unidades.CANONICAS)
    + "."
)


class UnidadNormalizada:
    """Normaliza la unidad al validar; `unidad_rara` dice si no está en la lista."""

    unidad_rara = False

    def clean_unidad(self):
        valor, reconocida = unidades.normalizar_unidad(self.cleaned_data.get("unidad", ""))
        self.unidad_rara = bool(valor) and not reconocida
        return valor


class PartidaForm(UnidadNormalizada, FormularioBootstrap, forms.ModelForm):
    grupos = [("Partida", ["codigo", "resumen", "unidad", "seccion", "texto"])]

    class Meta:
        model = Partida
        fields = ["codigo", "resumen", "unidad", "seccion", "texto"]
        widgets = {"texto": forms.Textarea(attrs={"rows": 4})}
        help_texts = {"unidad": AYUDA_UNIDAD}


class RecursoForm(UnidadNormalizada, FormularioBootstrap, forms.ModelForm):
    grupos = [("Recurso", ["codigo", "tipo", "unidad", "resumen", "precio", "fecha_precio",
                           "texto"])]  # fmt: skip

    class Meta:
        model = Recurso
        fields = ["codigo", "tipo", "unidad", "resumen", "precio", "fecha_precio", "texto"]
        help_texts = {"unidad": AYUDA_UNIDAD}
        widgets = {
            "texto": forms.Textarea(attrs={"rows": 3}),
            "fecha_precio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "precio": PrecioInput(attrs={"step": "0.0001"}),
        }


class LineaForm(forms.ModelForm):
    """Una línea del descompuesto: `elemento` es «r<id>» (recurso) o «p<id>» (partida auxiliar)."""

    elemento = forms.ChoiceField(label="Recurso o partida auxiliar")

    class Meta:
        model = LineaDescompuesto
        fields = ["factor", "rendimiento", "merma_pct"]

    def __init__(self, *args, opciones=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["elemento"].choices = [("", "—"), *opciones]
        if self.instance.pk:
            ln = self.instance
            elemento = f"r{ln.recurso_id}" if ln.recurso_id else f"p{ln.auxiliar_id}"
            self.initial["elemento"] = elemento
        for nombre, campo in self.fields.items():
            seleccion = nombre == "elemento"
            clase = "form-select form-select-sm" if seleccion else "form-control form-control-sm"
            campo.widget.attrs["class"] = clase
            if nombre != "elemento":
                campo.widget.attrs["class"] += " cifra"

    def clean(self):
        datos = super().clean()
        elemento = datos.get("elemento") or ""
        if not elemento:
            if self.has_changed() and not datos.get("DELETE"):
                self.add_error("elemento", "Elige un recurso o una partida auxiliar.")
            return datos
        tipo, pk = elemento[0], int(elemento[1:])
        self.instance.recurso_id = pk if tipo == "r" else None
        self.instance.auxiliar_id = pk if tipo == "p" else None
        return datos


class BaseLineasFormSet(BaseInlineFormSet):
    def __init__(self, *args, opciones=(), **kwargs):
        self.opciones = opciones
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "opciones": self.opciones}

    @property
    def empty_form(self):
        form = self.form(
            auto_id=self.auto_id, prefix=self.add_prefix("__prefix__"), empty_permitted=True,
            use_required_attribute=False, opciones=self.opciones,
        )  # fmt: skip
        self.add_fields(form, None)
        return form


LineasFormSet = inlineformset_factory(
    Partida, LineaDescompuesto, form=LineaForm, formset=BaseLineasFormSet,
    fk_name="partida", extra=3, can_delete=True,
)  # fmt: skip


def opciones_descompuesto(banco, excluir_partida=None):
    """Recursos y partidas del banco que pueden entrar en un descompuesto, agrupados."""
    recursos = [(f"r{r.pk}", f"{r.codigo} · {r.resumen} ({r.unidad})")
                for r in banco.recursos.order_by("codigo")]  # fmt: skip
    partidas = banco.partidas.order_by("codigo")
    if excluir_partida is not None and excluir_partida.pk:
        partidas = partidas.exclude(pk=excluir_partida.pk)
    auxiliares = [(f"p{p.pk}", f"{p.codigo} · {p.resumen} ({p.unidad})") for p in partidas]
    return [("Recursos", recursos), ("Partidas auxiliares", auxiliares)]


class ImportarBC3Form(forms.Form):
    fichero = forms.FileField(
        label="Fichero BC3",
        validators=[FileExtensionValidator(["bc3"], "El fichero debe tener extensión .bc3.")],
        help_text=(
            f"Formato FIEBDC-3 (.bc3), hasta {MAX_BC3_MB} MB. Antes de guardar verás un resumen."
        ),
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".bc3"}),
    )

    def clean_fichero(self):
        fichero = self.cleaned_data["fichero"]
        if fichero.size > MAX_BC3_MB * 1024 * 1024:
            raise forms.ValidationError(f"El fichero pasa de {MAX_BC3_MB} MB.")
        return fichero


class ImportarPlantillaForm(forms.Form):
    fichero = forms.FileField(
        label="Plantilla de cálculo",
        validators=[FileExtensionValidator(["xlsx", "xlsm"], "El fichero debe ser .xlsx o .xlsm.")],
        help_text=(
            f"Libro de Excel con una hoja por partida (.xlsx o .xlsm), hasta {MAX_PLANTILLA_MB} "
            "MB. No se ejecutan macros. Antes de guardar verás un resumen."
        ),
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".xlsx,.xlsm"}),
    )

    def clean_fichero(self):
        fichero = self.cleaned_data["fichero"]
        if fichero.size > MAX_PLANTILLA_MB * 1024 * 1024:
            raise forms.ValidationError(f"El fichero pasa de {MAX_PLANTILLA_MB} MB.")
        return fichero
