from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from turnos.services import enviar_recordatorios_email


class Command(BaseCommand):
    help = "Envia recordatorios por email para turnos confirmados proximos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horas",
            type=int,
            default=settings.TURNOS_RECORDATORIO_HORAS,
            help="Ventana de anticipacion para buscar turnos confirmados.",
        )

    def handle(self, *args, **options):
        horas = options["horas"]

        if horas <= 0:
            raise CommandError("La cantidad de horas debe ser mayor a cero.")

        resultado = enviar_recordatorios_email(horas_anticipacion=horas)

        self.stdout.write(
            self.style.SUCCESS(
                "Recordatorios encontrados: "
                f"{resultado.encontrados}. "
                f"Enviados: {resultado.enviados}. "
                f"Fallidos: {resultado.fallidos}."
            )
        )
