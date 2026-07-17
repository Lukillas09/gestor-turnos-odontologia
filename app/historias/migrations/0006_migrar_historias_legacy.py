from datetime import datetime, time

from django.db import migrations
from django.utils import timezone


def migrar_historias_legacy(apps, schema_editor):
    HistoriaClinica = apps.get_model("historias", "HistoriaClinica")
    paciente_ids = (
        HistoriaClinica.objects.order_by()
        .values_list("paciente_id", flat=True)
        .distinct()
    )

    for paciente_id in paciente_ids.iterator():
        historias = HistoriaClinica.objects.filter(paciente_id=paciente_id).order_by(
            "fecha",
            "creado_en",
            "pk",
        )

        for numero_asiento, historia in enumerate(historias.iterator(), start=1):
            fecha_hora = datetime.combine(historia.fecha, time.min)
            fecha_hora = timezone.make_aware(
                fecha_hora,
                timezone.get_current_timezone(),
            )
            finalizada_por_id = historia.actualizado_por_id or historia.creado_por_id
            finalizada_en = historia.actualizado_en or historia.creado_en or timezone.now()

            HistoriaClinica.objects.filter(pk=historia.pk).update(
                fecha_hora_atencion=fecha_hora,
                borrador=False,
                bloqueada_para_edicion=True,
                numero_asiento=numero_asiento,
                finalizada_en=finalizada_en,
                finalizada_por_id=finalizada_por_id,
                migrada_desde_legacy=True,
            )


def revertir_migracion_legacy(apps, schema_editor):
    HistoriaClinica = apps.get_model("historias", "HistoriaClinica")
    HistoriaClinica.objects.filter(migrada_desde_legacy=True).update(
        borrador=True,
        bloqueada_para_edicion=False,
        numero_asiento=None,
        finalizada_en=None,
        finalizada_por_id=None,
        migrada_desde_legacy=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("historias", "0005_historia_inmutable_esquema"),
    ]

    operations = [
        migrations.RunPython(
            migrar_historias_legacy,
            revertir_migracion_legacy,
        ),
    ]
