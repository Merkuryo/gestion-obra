"""Pantallas del banco de precios: banco propio (partidas y recursos), bancos externos e
importación de BC3 con vista previa. La lógica está en catalogos/servicios.py."""

import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from core.listas import Columna, preparar_lista
from core.permisos import ConPermiso, puede, puede_editar
from core.secciones import seccion_elegida

from . import servicios
from .formularios import (
    ImportarBC3Form,
    LineasFormSet,
    PartidaForm,
    RecursoForm,
    opciones_descompuesto,
)
from .models import Banco, Partida, Recurso

MODULO = "banco"
SESION_BC3 = "importacion_bc3"
MIGAS = [("Banco de precios", "/banco/")]


def _ve_importes(usuario):
    return puede(usuario, MODULO, "ver_importes")


def _pendiente(recurso):
    if not recurso.pendiente:
        return None
    return (recurso.get_pendiente_display(), "warning")


# ── Banco propio: partidas ─────────────────────────────────────────────────────

COLUMNAS_PARTIDAS = [
    Columna("codigo", "Código"),
    Columna("resumen", "Resumen"),
    Columna("unidad", "Ud"),
    Columna("seccion", "Sección", ordenable=False),
    Columna("precio", "Precio", numerica=True),
]


class ListaPartidas(ConPermiso, View):
    modulo = MODULO

    def get(self, request):
        lista = preparar_lista(
            request,
            Partida.objects.filter(banco=servicios.banco_propio()).select_related("seccion"),
            COLUMNAS_PARTIDAS,
            buscar_en=("codigo", "resumen", "texto"),
            url_fila=lambda p: reverse("catalogos:partida", args=[p.pk]),
        )
        contexto = {"lista": lista, "migas": [("Banco de precios", "")]}
        return render(request, "catalogos/partidas_lista.html", contexto)


class FichaPartida(ConPermiso, View):
    """Alta y ficha de una partida del banco propio, con su descompuesto editable."""

    modulo = MODULO

    def _objetos(self, pk):
        propio = servicios.banco_propio()
        partida = get_object_or_404(Partida, pk=pk, banco=propio) if pk else Partida(banco=propio)
        return propio, partida

    def _pintar(self, request, form, formset, partida):
        ve = _ve_importes(request.user)
        guardada = partida.pk is not None
        contexto = {
            "form": form,
            "formset": formset,
            "partida": partida if guardada else None,
            "filas": servicios.filas_descompuesto(partida, ve) if guardada else [],
            "ve_importes": ve,
            "url_lista": reverse("catalogos:partidas"),
            "migas": [*MIGAS, (str(partida) if guardada else "Nueva partida", "")],
        }
        return render(request, "catalogos/partida_form.html", contexto)

    def get(self, request, pk=None):
        propio, partida = self._objetos(pk)
        editable = puede_editar(request.user, MODULO)
        if not partida.pk and not editable:
            raise PermissionDenied
        form = PartidaForm(instance=partida if partida.pk else None, solo_lectura=not editable,
                           usuario=request.user)  # fmt: skip
        if not partida.pk:
            form.instance.banco = propio
        formset = (
            LineasFormSet(instance=partida, opciones=opciones_descompuesto(propio, partida))
            if editable
            else None
        )
        return self._pintar(request, form, formset, partida)

    def post(self, request, pk=None):
        propio, partida = self._objetos(pk)
        form = PartidaForm(request.POST, instance=partida, usuario=request.user)
        formset = LineasFormSet(
            request.POST, instance=partida, opciones=opciones_descompuesto(propio, partida)
        )
        if form.is_valid() and formset.is_valid():
            lineas = formset.save(commit=False)
            try:
                guardada = servicios.guardar_partida(
                    form.save(commit=False), lineas, formset.deleted_objects, request.user
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, f"Partida {guardada.codigo} guardada.")
                return redirect("catalogos:partida", pk=guardada.pk)
        return self._pintar(request, form, formset, partida)


# ── Banco propio: recursos ─────────────────────────────────────────────────────

COLUMNAS_RECURSOS = [
    Columna("codigo", "Código"),
    Columna("resumen", "Resumen"),
    Columna("tipo", "Tipo", valor=lambda r: r.get_tipo_display()),
    Columna("unidad", "Ud"),
    Columna("precio", "Precio", numerica=True),
    Columna("pendiente", "Pendiente", tipo="badge", ordenable=False, valor=_pendiente),
]


class ListaRecursos(ConPermiso, View):
    modulo = MODULO

    def get(self, request):
        lista = preparar_lista(
            request,
            Recurso.objects.filter(banco=servicios.banco_propio()),
            COLUMNAS_RECURSOS,
            buscar_en=("codigo", "resumen"),
            url_fila=lambda r: reverse("catalogos:recurso", args=[r.pk]),
        )
        migas = [*MIGAS, ("Recursos", "")]
        return render(request, "catalogos/recursos_lista.html", {"lista": lista, "migas": migas})


class FichaRecurso(ConPermiso, View):
    modulo = MODULO

    def _pintar(self, request, form, recurso):
        contexto = {
            "form": form,
            "recurso": recurso,
            "url_lista": reverse("catalogos:recursos"),
            "migas": [*MIGAS, ("Recursos", reverse("catalogos:recursos")),
                      (str(recurso or "Nuevo recurso"), "")],
        }  # fmt: skip
        return render(request, "catalogos/recurso_form.html", contexto)

    def _recurso(self, pk):
        return get_object_or_404(Recurso, pk=pk, banco=servicios.banco_propio()) if pk else None

    def get(self, request, pk=None):
        recurso = self._recurso(pk)
        editable = puede_editar(request.user, MODULO)
        if recurso is None and not editable:
            raise PermissionDenied
        form = RecursoForm(instance=recurso, solo_lectura=not editable, usuario=request.user)
        return self._pintar(request, form, recurso)

    def post(self, request, pk=None):
        recurso = self._recurso(pk)
        form = RecursoForm(request.POST, instance=recurso, usuario=request.user)
        if form.is_valid():
            guardado = form.save(commit=False)
            if guardado.pendiente and "precio" in form.changed_data:
                guardado.pendiente = ""  # ya tiene el precio de la empresa
            guardado = servicios.guardar_recurso(guardado, request.user)
            messages.success(request, f"Recurso {guardado.codigo} guardado.")
            return redirect("catalogos:recurso", pk=guardado.pk)
        return self._pintar(request, form, recurso)


# ── Bancos externos ────────────────────────────────────────────────────────────

COLUMNAS_BANCOS = [
    Columna("nombre", "Banco"),
    Columna("fichero", "Fichero"),
    Columna("version_formato", "Formato"),
    Columna("importado_en", "Importado", valor=lambda b: b.importado_en and b.importado_en.date()),
    Columna("num_partidas", "Partidas", numerica=True, decimales=0, ordenable=False),
]

COLUMNAS_EXTERNAS = [
    Columna("codigo", "Código"),
    Columna("resumen", "Resumen"),
    Columna("unidad", "Ud"),
    Columna("capitulo", "Capítulo", ordenable=False),
    Columna("precio", "Precio", numerica=True),
]


class ListaBancos(ConPermiso, View):
    modulo = MODULO

    def get(self, request):
        bancos = Banco.objects.filter(tipo=Banco.EXTERNO).annotate(num_partidas=Count("partidas"))
        lista = preparar_lista(
            request, bancos, COLUMNAS_BANCOS, buscar_en=("nombre", "fichero"),
            url_fila=lambda b: reverse("catalogos:banco", args=[b.pk]),
        )  # fmt: skip
        migas = [*MIGAS, ("Bancos externos", "")]
        return render(request, "catalogos/bancos_lista.html", {"lista": lista, "migas": migas})


class ContenidoBanco(ConPermiso, View):
    modulo = MODULO

    def get(self, request, pk):
        banco = get_object_or_404(Banco, pk=pk, tipo=Banco.EXTERNO)
        lista = preparar_lista(
            request,
            banco.partidas.filter(auxiliar=False).select_related("capitulo"),
            COLUMNAS_EXTERNAS,
            buscar_en=("codigo", "resumen", "texto"),
            url_fila=lambda p: reverse("catalogos:partida_externa", args=[banco.pk, p.pk]),
            por_seccion=False,  # los bancos externos no tienen sección
        )
        contexto = {
            "banco": banco,
            "lista": lista,
            "recuento": {
                "capitulos": banco.capitulos.count(),
                "auxiliares": banco.partidas.filter(auxiliar=True).count(),
                "recursos": banco.recursos.count(),
            },
            "migas": [*MIGAS, ("Bancos externos", reverse("catalogos:bancos")), (banco.nombre, "")],
        }
        return render(request, "catalogos/banco_contenido.html", contexto)


class PartidaExterna(ConPermiso, View):
    """Ficha de solo lectura de una partida de un banco externo, con su descompuesto."""

    modulo = MODULO

    def get(self, request, banco_pk, pk):
        partida = get_object_or_404(
            Partida.objects.select_related("banco", "capitulo"),
            pk=pk, banco_id=banco_pk, banco__tipo=Banco.EXTERNO,
        )  # fmt: skip
        ve = _ve_importes(request.user)
        contexto = {
            "partida": partida,
            "banco": partida.banco,
            "filas": servicios.filas_descompuesto(partida, ve),
            "ve_importes": ve,
            "puede_copiar": puede_editar(request.user, MODULO),
            "migas": [
                *MIGAS,
                ("Bancos externos", reverse("catalogos:bancos")),
                (partida.banco.nombre, reverse("catalogos:banco", args=[partida.banco_id])),
                (partida.codigo, ""),
            ],
        }
        return render(request, "catalogos/partida_externa.html", contexto)


class CopiarPartida(ConPermiso, View):
    """Copia una partida de un banco externo al banco propio. Solo POST."""

    modulo = MODULO
    http_method_names = ["post"]

    def post(self, request, banco_pk, pk):
        partida = get_object_or_404(Partida, pk=pk, banco_id=banco_pk, banco__tipo=Banco.EXTERNO)
        copia = servicios.copiar_al_banco_propio(
            partida, request.user, seccion=seccion_elegida(request.user)
        )
        messages.success(
            request,
            f"Partida copiada al banco propio como {copia.codigo}. Revisa la mano de obra y los "
            "materiales marcados como pendientes.",
        )
        return redirect("catalogos:partida", pk=copia.pk)


# ── Importación de BC3 ─────────────────────────────────────────────────────────


def _borrar_subida(request):
    datos = request.session.pop(SESION_BC3, None)
    if datos and default_storage.exists(datos["ruta"]):
        default_storage.delete(datos["ruta"])


class ImportarBC3(ConPermiso, View):
    """Subida del BC3 → vista previa (sin guardar nada) → confirmación."""

    modulo = MODULO
    migas = [*MIGAS, ("Importar BC3", "")]

    def get(self, request):
        if not puede_editar(request.user, MODULO):
            raise PermissionDenied
        return render(
            request, "catalogos/importar_bc3.html", {"form": ImportarBC3Form(), "migas": self.migas}
        )

    def post(self, request):
        if "confirmar" in request.POST:
            return self._confirmar(request)
        if "cancelar" in request.POST:
            _borrar_subida(request)
            messages.info(request, "Importación cancelada; no se ha guardado nada.")
            return redirect("catalogos:importar_bc3")
        form = ImportarBC3Form(request.POST, request.FILES)
        if not form.is_valid():
            contexto = {"form": form, "migas": self.migas}
            return render(request, "catalogos/importar_bc3.html", contexto)
        fichero = form.cleaned_data["fichero"]
        datos = fichero.read()
        _borrar_subida(request)
        ruta = default_storage.save(f"importaciones/bc3/{uuid.uuid4().hex}.bc3", ContentFile(datos))
        request.session[SESION_BC3] = {"ruta": ruta, "fichero": fichero.name}
        vista = servicios.preparar_bc3(datos, fichero.name)
        return render(
            request, "catalogos/vista_previa_bc3.html", {"vista": vista, "migas": self.migas}
        )

    def _confirmar(self, request):
        datos = request.session.get(SESION_BC3)
        if not datos or not default_storage.exists(datos["ruta"]):
            messages.warning(request, "La subida ha caducado: vuelve a elegir el fichero.")
            return redirect("catalogos:importar_bc3")
        with default_storage.open(datos["ruta"], "rb") as f:
            contenido = f.read()
        try:
            resumen = servicios.importar_bc3(contenido, datos["fichero"], request.user)
        except ValidationError as error:
            messages.warning(request, error.messages[0])
            return redirect("catalogos:importar_bc3")
        finally:
            _borrar_subida(request)
        accion = "importado" if resumen.nuevo else "actualizado"
        c, a, b = resumen.creados, resumen.actualizados, resumen.borrados
        messages.success(
            request,
            f"Banco «{resumen.banco.nombre}» {accion}: partidas {c['partidas']} nuevas, "
            f"{a['partidas']} actualizadas y {b['partidas']} quitadas; recursos {c['recursos']} "
            f"nuevos, {a['recursos']} actualizados y {b['recursos']} quitados.",
        )
        return redirect("catalogos:banco", pk=resumen.banco.pk)
