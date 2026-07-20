from django.core.management.base import BaseCommand

from turnos.models import TipoTurno

TIPOS_INICIALES = (
    ("Control", "control", "Revisión general programada.", "check", 10),
    ("Limpieza", "limpieza", "Limpieza dental programada.", "clinical", 20),
    ("Consulta", "consulta", "Consulta para evaluar una necesidad.", "info", 30),
)


class Command(BaseCommand):
    help = (
        "Crea un catálogo operativo inicial sin habilitar servicios ni duraciones "
        "para ningún profesional."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Informa qué crearía sin modificar la base de datos.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        creados = 0
        existentes = 0

        for nombre, slug, descripcion, icono, orden in TIPOS_INICIALES:
            if TipoTurno.objects.filter(slug=slug).exists():
                existentes += 1
                continue
            if dry_run:
                creados += 1
                self.stdout.write(f"Crearía: {nombre}")
                continue
            TipoTurno.objects.create(
                nombre=nombre,
                slug=slug,
                descripcion_publica=descripcion,
                icono=icono,
                orden_publico=orden,
                activo=True,
                visible_publicamente=False,
            )
            creados += 1

        modo = "Simulación" if dry_run else "Resultado"
        self.stdout.write(
            self.style.SUCCESS(
                f"{modo}: {creados} por crear/creados; {existentes} ya existentes. "
                "No se habilitaron reservas públicas."
            )
        )
