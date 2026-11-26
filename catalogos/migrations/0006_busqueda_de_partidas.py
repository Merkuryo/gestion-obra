"""Buscador de partidas: texto completo en español sin tildes y trigramas para las erratas.

- `es_sin_tildes`: configuración de búsqueda copiada de la española con `unaccent` antes de la
  raíz (plástica = plastica; pinturas = pintura).
- `f_unaccent`: unaccent inmutable, para poder usarlo en índices y disparadores.
- Disparador en las partidas: al insertar o al cambiar código, resumen, texto o capítulo,
  recalcula `busqueda` (pesos: A código y resumen, B capítulo, C texto largo) y
  `texto_busqueda` (código, resumen y capítulo en minúsculas y sin tildes, para trigramas).
  Así se mantiene igual al guardar una ficha que al importar miles de partidas de golpe.
- Disparador en los capítulos: si cambia su código o su nombre, se recalculan sus partidas.
"""

import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations, models

FUNCIONES = r"""
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

DROP TEXT SEARCH CONFIGURATION IF EXISTS es_sin_tildes;
CREATE TEXT SEARCH CONFIGURATION es_sin_tildes (COPY = pg_catalog.spanish);
ALTER TEXT SEARCH CONFIGURATION es_sin_tildes
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;

CREATE OR REPLACE FUNCTION catalogos_partida_busqueda() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    capitulo text;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.busqueda IS NOT NULL
       AND NEW.codigo IS NOT DISTINCT FROM OLD.codigo
       AND NEW.resumen IS NOT DISTINCT FROM OLD.resumen
       AND NEW.texto IS NOT DISTINCT FROM OLD.texto
       AND NEW.capitulo_id IS NOT DISTINCT FROM OLD.capitulo_id THEN
        NEW.busqueda := OLD.busqueda;
        NEW.texto_busqueda := OLD.texto_busqueda;
        RETURN NEW;
    END IF;
    IF NEW.capitulo_id IS NOT NULL THEN
        SELECT c.codigo || ' ' || c.resumen INTO capitulo
          FROM catalogos_capitulo c WHERE c.id = NEW.capitulo_id;
    END IF;
    capitulo := coalesce(capitulo, '');
    NEW.busqueda :=
        setweight(to_tsvector('es_sin_tildes', NEW.codigo || ' ' || NEW.resumen), 'A')
        || setweight(to_tsvector('es_sin_tildes', capitulo), 'B')
        || setweight(to_tsvector('es_sin_tildes', coalesce(NEW.texto, '')), 'C');
    NEW.texto_busqueda := lower(f_unaccent(NEW.codigo || ' ' || NEW.resumen || ' ' || capitulo));
    RETURN NEW;
END $$;

CREATE TRIGGER catalogos_partida_busqueda
    BEFORE INSERT OR UPDATE ON catalogos_partida
    FOR EACH ROW EXECUTE FUNCTION catalogos_partida_busqueda();

CREATE OR REPLACE FUNCTION catalogos_capitulo_busqueda() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.codigo IS DISTINCT FROM OLD.codigo OR NEW.resumen IS DISTINCT FROM OLD.resumen THEN
        UPDATE catalogos_partida SET busqueda = NULL WHERE capitulo_id = NEW.id;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER catalogos_capitulo_busqueda
    AFTER UPDATE ON catalogos_capitulo
    FOR EACH ROW EXECUTE FUNCTION catalogos_capitulo_busqueda();

-- Las partidas que ya había
UPDATE catalogos_partida SET busqueda = NULL;
"""

QUITAR = r"""
DROP TRIGGER IF EXISTS catalogos_capitulo_busqueda ON catalogos_capitulo;
DROP FUNCTION IF EXISTS catalogos_capitulo_busqueda();
DROP TRIGGER IF EXISTS catalogos_partida_busqueda ON catalogos_partida;
DROP FUNCTION IF EXISTS catalogos_partida_busqueda();
DROP TEXT SEARCH CONFIGURATION IF EXISTS es_sin_tildes;
DROP FUNCTION IF EXISTS f_unaccent(text);
"""


class Migration(migrations.Migration):
    dependencies = [("catalogos", "0005_metro_es_metro_lineal")]

    operations = [
        UnaccentExtension(),
        TrigramExtension(),
        migrations.AddField(
            model_name="partida",
            name="busqueda",
            field=django.contrib.postgres.search.SearchVectorField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="partida",
            name="texto_busqueda",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.RunSQL(FUNCIONES, QUITAR),
        migrations.AddIndex(
            model_name="partida",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["busqueda"], name="partida_busqueda_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="partida",
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass("texto_busqueda", name="gin_trgm_ops"),
                name="partida_trigramas_gin",
            ),
        ),
    ]
