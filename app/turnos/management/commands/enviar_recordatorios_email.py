from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from turnos.services import enviar_recordatorios_email


class Command(BaseCommand):
    help = "Envía recordatorios por email para turnos confirmados próximos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horas",
            type=int,
            default=settings.TURNOS_RECORDATORIO_HORAS,
            help="Ventana de anticipacion para buscar turnos confirmados.",
        )
        parser.add_argument(
            "--fallar-si-hay-errores",
            action="store_true",
            help="Finaliza con error si algun recordatorio no pudo enviarse.",
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

        if options["fallar_si_hay_errores"] and resultado.fallidos:
            raise CommandError("Hubo recordatorios que no pudieron enviarse.")
