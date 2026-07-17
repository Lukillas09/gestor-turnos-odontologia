from django.db import migrations


SQL_INSTALAR = """
CREATE OR REPLACE FUNCTION indicaciones_rechazar_mutacion_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'El registro append-only de indicaciones no se puede modificar ni eliminar.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER indicaciones_plantilla_version_append_only
BEFORE UPDATE OR DELETE ON indicaciones_plantillaindicacionversion
FOR EACH ROW EXECUTE FUNCTION indicaciones_rechazar_mutacion_append_only();

CREATE OR REPLACE FUNCTION indicaciones_rechazar_borrado_clinico()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'El documento clínico no se puede eliminar físicamente.'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER indicaciones_documento_sin_borrado
BEFORE DELETE ON indicaciones_indicacionpaciente
FOR EACH ROW EXECUTE FUNCTION indicaciones_rechazar_borrado_clinico();

CREATE TRIGGER indicaciones_plantilla_sin_borrado
BEFORE DELETE ON indicaciones_plantillaindicacion
FOR EACH ROW EXECUTE FUNCTION indicaciones_rechazar_borrado_clinico();

CREATE OR REPLACE FUNCTION indicaciones_proteger_documento_emitido()
RETURNS trigger AS $$
BEGIN
    IF OLD.estado = 'anulada' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Una indicación anulada es inmutable.'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.estado = 'emitida' THEN
        IF NEW.estado NOT IN ('emitida', 'anulada') THEN
            RAISE EXCEPTION 'Una indicación emitida no puede volver a borrador.'
                USING ERRCODE = '55000';
        END IF;

        IF OLD.paciente_id IS DISTINCT FROM NEW.paciente_id
            OR OLD.odontologo_id IS DISTINCT FROM NEW.odontologo_id
            OR OLD.historia_clinica_id IS DISTINCT FROM NEW.historia_clinica_id
            OR OLD.turno_id IS DISTINCT FROM NEW.turno_id
            OR OLD.plantilla_id IS DISTINCT FROM NEW.plantilla_id
            OR OLD.plantilla_version IS DISTINCT FROM NEW.plantilla_version
            OR OLD.titulo IS DISTINCT FROM NEW.titulo
            OR OLD.procedimiento IS DISTINCT FROM NEW.procedimiento
            OR OLD.contenido IS DISTINCT FROM NEW.contenido
            OR OLD.pautas_alarma IS DISTINCT FROM NEW.pautas_alarma
            OR OLD.recomendaciones_control IS DISTINCT FROM NEW.recomendaciones_control
            OR OLD.observaciones_personalizadas IS DISTINCT FROM NEW.observaciones_personalizadas
            OR OLD.proximo_control_en IS DISTINCT FROM NEW.proximo_control_en
            OR OLD.snapshot_paciente IS DISTINCT FROM NEW.snapshot_paciente
            OR OLD.snapshot_profesional IS DISTINCT FROM NEW.snapshot_profesional
            OR OLD.snapshot_consultorio IS DISTINCT FROM NEW.snapshot_consultorio
            OR OLD.snapshot_documento IS DISTINCT FROM NEW.snapshot_documento
            OR OLD.emitida_en IS DISTINCT FROM NEW.emitida_en
            OR OLD.emitida_por_id IS DISTINCT FROM NEW.emitida_por_id
            OR OLD.pdf IS DISTINCT FROM NEW.pdf
            OR OLD.pdf_sha256 IS DISTINCT FROM NEW.pdf_sha256
            OR OLD.sello_integridad IS DISTINCT FROM NEW.sello_integridad
            OR OLD.referencia_integridad IS DISTINCT FROM NEW.referencia_integridad
            OR OLD.reemplaza_a_id IS DISTINCT FROM NEW.reemplaza_a_id
            OR OLD.creado_por_id IS DISTINCT FROM NEW.creado_por_id
            OR OLD.creado_en IS DISTINCT FROM NEW.creado_en
        THEN
            RAISE EXCEPTION 'El contenido de una indicación emitida es inmutable.'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER indicaciones_documento_emitido_inmutable
BEFORE UPDATE ON indicaciones_indicacionpaciente
FOR EACH ROW EXECUTE FUNCTION indicaciones_proteger_documento_emitido();
"""


SQL_REVERTIR = """
DROP TRIGGER IF EXISTS indicaciones_documento_emitido_inmutable
    ON indicaciones_indicacionpaciente;
DROP TRIGGER IF EXISTS indicaciones_plantilla_sin_borrado
    ON indicaciones_plantillaindicacion;
DROP TRIGGER IF EXISTS indicaciones_documento_sin_borrado
    ON indicaciones_indicacionpaciente;
DROP TRIGGER IF EXISTS indicaciones_plantilla_version_append_only
    ON indicaciones_plantillaindicacionversion;

DROP FUNCTION IF EXISTS indicaciones_proteger_documento_emitido();
DROP FUNCTION IF EXISTS indicaciones_rechazar_borrado_clinico();
DROP FUNCTION IF EXISTS indicaciones_rechazar_mutacion_append_only();
"""


def instalar_protecciones(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_INSTALAR)


def revertir_protecciones(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_REVERTIR)


class Migration(migrations.Migration):
    dependencies = [("indicaciones", "0001_initial")]

    operations = [migrations.RunPython(instalar_protecciones, revertir_protecciones)]
