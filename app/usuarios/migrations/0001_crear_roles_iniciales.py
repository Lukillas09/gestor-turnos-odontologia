from django.db import migrations


def crear_roles_iniciales(apps, schema_editor):
    Group = apps.get_model("auth", "Group")

    for nombre_rol in ("Recepcionista", "Odontologo", "Administrador"):
        Group.objects.get_or_create(name=nombre_rol)


def eliminar_roles_iniciales(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(
        name__in=["Recepcionista", "Odontologo", "Administrador"],
        user__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(crear_roles_iniciales, eliminar_roles_iniciales),
    ]
