from django.db import migrations


SQL_INSTALAR = """
CREATE OR REPLACE FUNCTION historias_rechazar_mutacion_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'El registro clínico append-only no se puede modificar ni eliminar.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER historias_version_append_only
BEFORE UPDATE OR DELETE ON historias_historiaclinicaversion
FOR EACH ROW EXECUTE FUNCTION historias_rechazar_mutacion_append_only();

CREATE TRIGGER historias_enmienda_append_only
BEFORE UPDATE OR DELETE ON historias_historiaclinicaenmienda
FOR EACH ROW EXECUTE FUNCTION historias_rechazar_mutacion_append_only();

CREATE OR REPLACE FUNCTION historias_rechazar_borrado_clinico()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'El registro clínico no se puede eliminar físicamente.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER historias_historia_sin_borrado
BEFORE DELETE ON historias_historiaclinica
FOR EACH ROW EXECUTE FUNCTION historias_rechazar_borrado_clinico();

CREATE TRIGGER historias_adjunto_sin_borrado
BEFORE DELETE ON historias_historiaclinicaadjunto
FOR EACH ROW EXECUTE FUNCTION historias_rechazar_borrado_clinico();

CREATE OR REPLACE FUNCTION historias_rechazar_cambio_finalizada()
RETURNS trigger AS $$
BEGIN
    IF OLD.bloqueada_para_edicion IS TRUE AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'La entrada clínica finalizada es inmutable; use una enmienda.'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER historias_historia_finalizada_inmutable
BEFORE UPDATE ON historias_historiaclinica
FOR EACH ROW EXECUTE FUNCTION historias_rechazar_cambio_finalizada();
"""


SQL_REVERTIR = """
DROP TRIGGER IF EXISTS historias_historia_finalizada_inmutable
    ON historias_historiaclinica;
DROP TRIGGER IF EXISTS historias_adjunto_sin_borrado
    ON historias_historiaclinicaadjunto;
DROP TRIGGER IF EXISTS historias_historia_sin_borrado
    ON historias_historiaclinica;
DROP TRIGGER IF EXISTS historias_enmienda_append_only
    ON historias_historiaclinicaenmienda;
DROP TRIGGER IF EXISTS historias_version_append_only
    ON historias_historiaclinicaversion;

DROP FUNCTION IF EXISTS historias_rechazar_cambio_finalizada();
DROP FUNCTION IF EXISTS historias_rechazar_borrado_clinico();
DROP FUNCTION IF EXISTS historias_rechazar_mutacion_append_only();
"""


def instalar_protecciones(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_INSTALAR)


def revertir_protecciones(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_REVERTIR)


class Migration(migrations.Migration):
    dependencies = [
        ("historias", "0006_migrar_historias_legacy"),
    ]

    operations = [
        migrations.RunPython(
            instalar_protecciones,
            revertir_protecciones,
        ),
    ]
