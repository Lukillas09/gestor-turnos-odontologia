from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import consultorio.models
import consultorio.validators


def crear_configuracion_default(apps, schema_editor):
    ConfiguracionConsultorio = apps.get_model("consultorio", "ConfiguracionConsultorio")
    ConfiguracionConsultorio.objects.get_or_create(pk=1)


def eliminar_configuracion_default(apps, schema_editor):
    ConfiguracionConsultorio = apps.get_model("consultorio", "ConfiguracionConsultorio")
    ConfiguracionConsultorio.objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionConsultorio",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre_comercial", models.CharField(default="Gestor de Turnos", max_length=120)),
                ("nombre_corto", models.CharField(blank=True, max_length=80)),
                (
                    "logo",
                    models.FileField(
                        blank=True,
                        upload_to=consultorio.models.ruta_logo_consultorio,
                        validators=[consultorio.validators.validar_logo_consultorio],
                    ),
                ),
                ("direccion", models.CharField(blank=True, max_length=180)),
                ("localidad", models.CharField(blank=True, max_length=100)),
                ("provincia", models.CharField(blank=True, max_length=100)),
                ("telefono", models.CharField(blank=True, max_length=40)),
                ("whatsapp", models.CharField(blank=True, max_length=40)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("horario_atencion", models.TextField(blank=True)),
                (
                    "titulo_portada",
                    models.CharField(
                        default="Reservá tu turno odontológico de forma simple",
                        max_length=160,
                    ),
                ),
                (
                    "texto_bienvenida",
                    models.TextField(
                        default=(
                            "Elegí un odontólogo, seleccioná un horario disponible y enviá tu solicitud. "
                            "El consultorio te avisará cuando el turno quede confirmado."
                        )
                    ),
                ),
                ("politica_cancelacion", models.TextField(blank=True)),
                (
                    "color_principal",
                    models.CharField(
                        default="#2563EB",
                        max_length=7,
                        validators=[consultorio.validators.validar_color_hex],
                    ),
                ),
                ("mostrar_direccion", models.BooleanField(default=True)),
                ("mostrar_telefono", models.BooleanField(default=True)),
                ("mostrar_whatsapp", models.BooleanField(default=True)),
                ("mostrar_email", models.BooleanField(default=True)),
                ("mostrar_horario_atencion", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizado_por",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="configuraciones_consultorio_actualizadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Configuración del consultorio",
                "verbose_name_plural": "Configuración del consultorio",
            },
        ),
        migrations.AddConstraint(
            model_name="configuracionconsultorio",
            constraint=models.CheckConstraint(
                condition=models.Q(("id", 1)),
                name="configuracion_consultorio_pk_unico",
            ),
        ),
        migrations.RunPython(crear_configuracion_default, eliminar_configuracion_default),
    ]
