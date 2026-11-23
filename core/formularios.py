"""Base de formularios para los componentes de templates/componentes.

Pone las clases de Bootstrap a cada campo, agrupa los campos en pestañas y permite
mostrar la ficha en solo lectura. Con `usuario`, los campos que no puede ver (importes, márgenes,
nóminas: core.permisos.campos_ocultos) salen como «(oculto)», no editables, y al guardar
conservan su valor. Sin `usuario` se ocultan todos esos campos. En un alta con `usuario`, el
campo `seccion` sale con la sección elegida en el selector de la barra (editable). Uso:

    class SeccionForm(FormularioBootstrap, forms.ModelForm):
        grupos = [("General", ["codigo", "nombre"]), ("Económico", ["pct_beneficio_defecto"])]
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .models import PermisoRol, Rol
from .permisos import ACCIONES, OCULTO, PARTES, campos_ocultos
from .secciones import CAMPO as CAMPO_SECCION
from .secciones import seccion_elegida


class FormularioBootstrap:
    # Lista de (título, [nombres de campo]). Con más de un grupo se muestran pestañas.
    # Los campos que no estén en ningún grupo van en uno final "Otros".
    grupos: list[tuple[str, list[str]]] = []

    def __init__(self, *args, solo_lectura=False, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.solo_lectura = solo_lectura
        for campo in self.fields.values():
            self._poner_clases(campo)
            if solo_lectura:
                campo.disabled = True
        self.ocultos = self._ocultar(usuario)
        self._seccion_por_defecto(usuario, kwargs.get("initial") or {})

    def _seccion_por_defecto(self, usuario, inicial):
        """En un alta, la sección elegida en el selector, salvo que la vista ya traiga otra."""
        instancia = getattr(self, "instance", None)
        if (
            usuario is None
            or self.is_bound
            or instancia is None
            or not instancia._state.adding
            or CAMPO_SECCION not in self.fields
            or CAMPO_SECCION in self.ocultos
            or CAMPO_SECCION in inicial
            or getattr(instancia, f"{CAMPO_SECCION}_id", None)
        ):
            return
        seccion = seccion_elegida(usuario)
        if seccion is not None:
            self.initial[CAMPO_SECCION] = seccion.pk

    def _ocultar(self, usuario):
        """Lo que no puedes ver, no lo puedes editar. Un campo desactivado ignora el POST y se
        valida y guarda con el valor que ya tenía (o el de por defecto en un alta)."""
        instancia = getattr(self, "instance", None)
        if instancia is None:
            return {}
        ocultos = {
            n: permiso
            for n, permiso in campos_ocultos(usuario, instancia).items()
            if n in self.fields
        }
        for nombre, permiso in ocultos.items():
            campo = self.fields[nombre]
            campo.disabled = True
            campo.widget = CampoOcultoInput(attrs={"class": "form-control"})
            campo.help_text = f"Necesitas el permiso «{permiso}» para verlo."
        return ocultos

    @staticmethod
    def _poner_clases(campo):
        widget = campo.widget
        if getattr(widget, "sin_clases", False):
            return
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


class SelectorMultiple(forms.CheckboxSelectMultiple):
    """Selección de varios valores con búsqueda (componentes/_selector_multiple.html).

    Sin JavaScript es una lista de casillas; con Alpine.js añade la búsqueda (sin tildes ni
    mayúsculas), las etiquetas de lo elegido y el manejo por teclado: ↓ desde la búsqueda pasa
    a las opciones, ↑/↓ entre ellas, espacio marca, Intro en la búsqueda marca la primera que
    coincide y Escape limpia la búsqueda.
    """

    template_name = "componentes/_selector_multiple.html"
    sin_clases = True  # las clases de Bootstrap las pone la plantilla


class CampoOcultoInput(forms.TextInput):
    """Muestra «(oculto)» y nunca el valor guardado."""

    def format_value(self, value):
        return OCULTO


class ColorInput(forms.TextInput):
    """Selector de color del navegador (<input type="color">) para valores #RRGGBB."""

    input_type = "color"


class CampoMayusculas(forms.CharField):
    """Texto que se convierte a MAYÚSCULAS antes de validar (p. ej. códigos de sección)."""

    def to_python(self, value):
        valor = super().to_python(value)
        return valor.upper() if valor else valor


class RolForm(FormularioBootstrap, forms.ModelForm):
    """Datos del rol, permisos propios y usuarios. Los permisos por parte van en PermisosRolForm."""

    usuarios = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=SelectorMultiple,
        help_text="Usuarios que tienen este rol.",
    )

    grupos = [
        ("General", ["nombre", "descripcion", "activo"]),
        ("Permisos propios", ["ver_nominas", "ver_margenes"]),
        ("Usuarios", ["usuarios"]),
    ]

    class Meta:
        model = Rol
        fields = ["nombre", "descripcion", "activo", "ver_nominas", "ver_margenes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        usuarios = get_user_model().objects.filter(is_active=True).order_by("username")
        self.fields["usuarios"].queryset = usuarios
        self.fields["usuarios"].label_from_instance = lambda u: (
            f"{u.get_full_name()} ({u.username})" if u.get_full_name() else u.username
        )
        if self.instance.pk:
            self.initial.setdefault("usuarios", list(self.instance.grupo.user_set.all()))
        if self.instance.es_plantilla:
            self.fields["nombre"].disabled = True
            self.fields["nombre"].help_text = "Rol de serie: se edita y se duplica, no se renombra."

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        otros = Group.objects.filter(name__iexact=nombre)
        if self.instance.pk:
            otros = otros.exclude(pk=self.instance.grupo_id)
        if otros.exists():
            raise forms.ValidationError("Ya hay un rol o grupo con ese nombre.")
        return nombre


class PermisosRolForm(forms.Form):
    """Tabla de partes × acciones, con el ámbito de cada parte."""

    def __init__(self, *args, permisos=None, **kwargs):
        super().__init__(*args, **kwargs)
        permisos = permisos or {}
        for parte in PARTES:
            actual = permisos.get(parte, {})
            for accion in ACCIONES:
                self.fields[f"{parte}__{accion}"] = forms.BooleanField(
                    label=ACCIONES[accion],
                    required=False,
                    initial=actual.get(accion, False),
                    widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
                )
            self.fields[f"{parte}__ambito"] = forms.ChoiceField(
                label="Ámbito",
                choices=PermisoRol.AMBITOS,
                initial=actual.get("ambito", PermisoRol.TODAS),
                widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
            )

    def filas(self):
        """Para la plantilla: una fila por parte con sus casillas y el ámbito."""
        return [
            {
                "nombre": nombre,
                "acciones": [self[f"{parte}__{a}"] for a in ACCIONES],
                "ambito": self[f"{parte}__ambito"],
            }
            for parte, nombre in PARTES.items()
        ]

    def como_dict(self):
        datos = self.cleaned_data
        return {
            parte: {
                **{a: datos[f"{parte}__{a}"] for a in ACCIONES},
                "ambito": datos[f"{parte}__ambito"],
            }
            for parte in PARTES
        }
