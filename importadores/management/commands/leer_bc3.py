"""python manage.py leer_bc3 <fichero>: resumen de un BC3 sin guardar nada."""

from django.core.management.base import BaseCommand, CommandError

from core.textos import contar
from importadores.bc3 import leer_bc3

MAX_INCIDENCIAS = 50


class Command(BaseCommand):
    help = "Lee un fichero BC3 (FIEBDC-3) y muestra su resumen, avisos y errores. No guarda nada."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("fichero", help="Ruta del fichero .bc3")
        parser.add_argument(
            "--arbol", action="store_true", help="Muestra también capítulos y partidas"
        )
        parser.add_argument(
            "--todas",
            action="store_true",
            help=f"Muestra todos los avisos (por defecto, los {MAX_INCIDENCIAS} primeros)",
        )

    def handle(self, fichero, arbol=False, todas=False, **opciones):
        r = leer_bc3(fichero)
        escribir = self.stdout.write

        escribir(f"Fichero: {fichero}")
        if r.version or r.programa:
            escribir(f"Versión: {r.version or '—'} · Programa: {r.programa or '—'}")
        juego = r.juego_de_caracteres or "sin indicar"
        escribir(f"Juego de caracteres: {juego} (leído como {r.codificacion})")
        if r.tipo_informacion:
            escribir(f"Tipo de información: {r.nombre_tipo_informacion}")
        if r.raiz:
            escribir(f"Raíz: {r.raiz.codigo} — {r.raiz.concepto.resumen}")

        n = r.recuento()
        escribir(
            f"Conceptos: {n['conceptos']} · Capítulos: {n['capitulos']} · "
            f"Partidas: {n['partidas']} · Descompuestos: {n['descompuestos']} "
            f"({n['lineas_descompuestas']} líneas) · Mediciones: {n['mediciones']} · "
            f"Textos: {n['textos']}"
        )

        if arbol:
            raices = r.raiz.hijos if r.raiz else r.capitulos
            for nodo in raices:
                self._pintar(nodo, 1)

        for titulo, lista, estilo in (
            ("Avisos", r.avisos, self.style.WARNING),
            ("Errores", r.errores, self.style.ERROR),
        ):
            if not lista:
                continue
            escribir(estilo(f"{titulo} ({len(lista)}):"))
            mostradas = lista if todas else lista[:MAX_INCIDENCIAS]
            for incidencia in mostradas:
                escribir(f"  {incidencia}")
            if len(mostradas) < len(lista):
                escribir(f"  … y {len(lista) - len(mostradas)} más (usa --todas).")

        escribir("No se ha guardado nada.")
        if r.errores:
            raise CommandError(f"El fichero tiene {contar(len(r.errores), 'error', 'errores')}.")

    def _pintar(self, nodo, nivel):
        if nodo.clase not in ("capitulo", "partida"):
            return
        sangria = "  " * nivel
        c = nodo.concepto
        if nodo.clase == "capitulo":
            self.stdout.write(f"{sangria}{c.codigo} {c.resumen}")
        else:
            medicion = nodo.medicion.total if nodo.medicion else nodo.rendimiento
            precio = "" if c.precio is None else f" · {c.precio}"
            self.stdout.write(f"{sangria}{c.codigo} {c.unidad} {c.resumen} · {medicion}{precio}")
        for hijo in nodo.hijos:
            self._pintar(hijo, nivel + 1)
