from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("turnos", "0015_alter_solicitudturnopublica_turno"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExcepcionAgenda",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("vacaciones", "Vacaciones"),
                            ("feriado", "Feriado"),
                            ("capacitacion", "Capacitación"),
                            ("ausencia_personal", "Ausencia personal"),
                            ("cierre_consultorio", "Cierre del consultorio"),
                            ("bloqueo_parcial", "Bloqueo parcial"),
                            ("otro", "Otro"),
                        ],
                        max_length=30,
                    ),
                ),
                ("fecha_desde", models.DateField()),
                ("fecha_hasta", models.DateField()),
                ("todo_el_dia", models.BooleanField(default=True)),
                ("hora_inicio", models.TimeField(blank=True, null=True)),
                ("hora_fin", models.TimeField(blank=True, null=True)),
                ("motivo", models.CharField(max_length=200)),
                (
                    "mensaje_publico",
                    models.CharField(
                        blank=True,
                        help_text="Mensaje opcional para indicar el motivo operativo sin exponer detalles internos.",
                        max_length=200,
                    ),
                ),
                ("activo", models.BooleanField(default=True)),
                ("desactivada_en", models.DateTimeField(blank=True, editable=False, null=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizada_por",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="excepciones_agenda_actualizadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "creada_por",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="excepciones_agenda_creadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "desactivada_por",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="excepciones_agenda_desactivadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "odontologo",
                    models.ForeignKey(
                        blank=True,
                        help_text="Dejar vacío para bloquear todo el consultorio.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="excepciones_agenda",
                        to="turnos.odontologo",
                    ),
                ),
            ],
            options={
                "verbose_name": "Excepción de agenda",
                "verbose_name_plural": "Excepciones de agenda",
                "ordering": ["-activo", "fecha_desde", "hora_inicio", "odontologo"],
            },
        ),
        migrations.AddIndex(
            model_name="excepcionagenda",
            index=models.Index(fields=["activo", "fecha_desde", "fecha_hasta"], name="turnos_exce_activo_cffef9_idx"),
        ),
        migrations.AddIndex(
            model_name="excepcionagenda",
            index=models.Index(
                fields=["odontologo", "activo", "fecha_desde", "fecha_hasta"],
                name="turnos_exce_odontol_b17b9f_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="excepcionagenda",
            constraint=models.CheckConstraint(
                condition=models.Q(("fecha_hasta__gte", models.F("fecha_desde"))),
                name="excepcion_agenda_fecha_hasta_gte_desde",
            ),
        ),
        migrations.AddConstraint(
            model_name="excepcionagenda",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("hora_fin__isnull", True), ("hora_inicio__isnull", True), ("todo_el_dia", True))
                    | models.Q(
                        ("hora_fin__gt", models.F("hora_inicio")),
                        ("hora_fin__isnull", False),
                        ("hora_inicio__isnull", False),
                        ("todo_el_dia", False),
                    )
                ),
                name="excepcion_agenda_horario_consistente",
            ),
        ),
    ]
