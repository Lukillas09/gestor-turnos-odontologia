from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from turnos.models import AccionPublicaTurno, DesafioAccesoPublicoTurnos


class Command(BaseCommand):
    help = "Elimina desafios OTP expirados y permisos publicos de turnos inactivos."

    def handle(self, *args, **options):
        ahora = timezone.now()
        desafios_eliminados, _ = DesafioAccesoPublicoTurnos.objects.filter(
            expira_en__lt=ahora,
        ).delete()
        acciones_eliminadas, _ = AccionPublicaTurno.objects.filter(
            Q(expira_en__lt=ahora) | Q(utilizado_en__isnull=False) | Q(revocado_en__isnull=False)
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Desafios eliminados: {desafios_eliminados}; "
                f"acciones eliminadas: {acciones_eliminadas}"
            )
        )
