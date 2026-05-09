from django.db import migrations


PERMISOS_HISTORIA_CLINICA = [
    ("historias", "historiaclinica", ["view", "add", "change"]),
]


def asignar_permisos_historias(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    grupo, _ = Group.objects.get_or_create(name="Odontologo")

    for app_label, model_name, acciones in PERMISOS_HISTORIA_CLINICA:
        content_type, _ = ContentType.objects.get_or_create(
            app_label=app_label,
            model=model_name,
        )

        for accion in acciones:
            codename = f"{accion}_{model_name}"
            permiso, _ = Permission.objects.get_or_create(
                content_type=content_type,
                codename=codename,
                defaults={"name": f"Can {accion} {model_name}"},
            )
            grupo.permissions.add(permiso)


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("historias", "0001_initial"),
        ("usuarios", "0002_asignar_permisos_roles"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos_historias, migrations.RunPython.noop),
    ]
