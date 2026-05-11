from django.db import migrations, models

import turnos.models


class Migration(migrations.Migration):

    dependencies = [
        ("turnos", "0005_odontologo_foto_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="odontologo",
            name="foto_perfil",
            field=models.FileField(
                blank=True,
                upload_to=turnos.models.ruta_foto_odontologo,
                validators=[turnos.models.validar_foto_odontologo],
            ),
        ),
        migrations.AddField(
            model_name="odontologo",
            name="foto_posicion_x",
            field=models.PositiveSmallIntegerField(default=50),
        ),
        migrations.AddField(
            model_name="odontologo",
            name="foto_posicion_y",
            field=models.PositiveSmallIntegerField(default=50),
        ),
    ]
