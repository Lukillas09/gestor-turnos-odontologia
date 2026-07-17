from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from indicaciones.emails import enviar_indicacion_por_email
from indicaciones.permissions import indicaciones_habilitadas
from indicaciones.selectors import indicaciones_pendientes_de_email


class Command(BaseCommand):
    help = "Reintenta emails pendientes de indicaciones emitidas sin exponer contenido clínico."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)
        parser.add_argument("--max-intentos", type=int, default=5)
        parser.add_argument("--fallar-si-hay-errores", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if not indicaciones_habilitadas():
            raise CommandError("El módulo de indicaciones postoperatorias está deshabilitado.")

        limite = options["limite"]
        max_intentos = options["max_intentos"]
        if limite <= 0:
            raise CommandError("--limite debe ser mayor a cero.")
        if max_intentos <= 0:
            raise CommandError("--max-intentos debe ser mayor a cero.")

        enviados = 0
        errores = 0
        with transaction.atomic():
            pendientes = list(indicaciones_pendientes_de_email(max_intentos=max_intentos)[:limite])
            if options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry-run: {len(pendientes)} indicación(es) elegibles para reintento."
                    )
                )
                return
            for indicacion in pendientes:
                if enviar_indicacion_por_email(
                    indicacion_id=indicacion.pk,
                    automatico=True,
                    forzar=False,
                ):
                    enviados += 1
                else:
                    errores += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Reintentos procesados: {enviados + errores}; enviados: {enviados}; "
                f"errores: {errores}."
            )
        )
        if errores and options["fallar_si_hay_errores"]:
            raise CommandError("Uno o más reintentos no pudieron completarse.")
