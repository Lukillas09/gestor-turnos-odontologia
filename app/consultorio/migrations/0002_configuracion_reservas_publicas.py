from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("consultorio", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionconsultorio",
            name="anticipacion_minima_reserva_publica_minutos",
            field=models.PositiveIntegerField(default=120),
        ),
        migrations.AddField(
            model_name="configuracionconsultorio",
            name="permitir_reserva_publica_mismo_dia",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="configuracionconsultorio",
            name="ventana_reserva_publica_dias",
            field=models.PositiveSmallIntegerField(default=14),
        ),
    ]
