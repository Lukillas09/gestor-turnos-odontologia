from django.db import migrations


PERMISOS_POR_ROL = {
    "Recepcionista": [
        ("pacientes", "paciente", ["view", "add", "change"]),
        ("turnos", "turno", ["view", "add", "change"]),
        ("turnos", "odontologo", ["view"]),
    ],
    "Odontologo": [
        ("turnos", "turno", ["view"]),
    ],
    "Administrador": [
        ("turnos", "odontologo", ["view", "add", "change"]),
        ("turnos", "disponibilidadodontologo", ["view", "add", "change", "delete"]),
    ],
}


def asignar_permisos_roles(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    for nombre_rol, permisos_configurados in PERMISOS_POR_ROL.items():
        grupo, _ = Group.objects.get_or_create(name=nombre_rol)

        for app_label, model_name, acciones in permisos_configurados:
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
        ("usuarios", "0001_crear_roles_iniciales"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos_roles, migrations.RunPython.noop),
    ]
