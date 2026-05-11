from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turnos", "0004_turno_recordatorio_email_enviado_en_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="odontologo",
            name="foto_url",
            field=models.URLField(blank=True),
        ),
    ]
