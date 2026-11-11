from django.db import migrations

# Los cuerpos de estas funciones solo usan OLD/NEW y RAISE: no referencian ningún objeto de
# esquema, así que alcanza con pg_catalog (operadores y tipos básicos). pg_temp va al final para
# que ningún objeto temporal pueda sombrearlos. Solo cambia metadatos; el comportamiento es el
# mismo. Cualquier CREATE OR REPLACE futuro de estas funciones debe repetir el SET.
FUNCIONES = (
    "indicaciones_rechazar_mutacion_append_only",
    "indicaciones_rechazar_borrado_clinico",
    "indicaciones_proteger_documento_emitido",
)


def fijar_search_path(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    for funcion in FUNCIONES:
        schema_editor.execute(f"ALTER FUNCTION {funcion}() SET search_path = pg_catalog, pg_temp;")


def restablecer_search_path(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    for funcion in FUNCIONES:
        schema_editor.execute(f"ALTER FUNCTION {funcion}() RESET search_path;")


class Migration(migrations.Migration):
    dependencies = [("indicaciones", "0002_protecciones_postgresql")]

    operations = [migrations.RunPython(fijar_search_path, restablecer_search_path)]
