from django.core.management.base import BaseCommand

from pacientes.services import asegurar_paciente_asociado_a_odontologo
from turnos.models import Turno


class Command(BaseCommand):
    help = "Reconstruye asociaciones paciente-odontólogo a partir de turnos existentes."

    def handle(self, *args, **options):
        turnos = Turno.objects.select_related("paciente", "odontologo").order_by(
            "paciente_id", "odontologo_id"
        )
        asociaciones = 0
        pares_procesados = set()

        for turno in turnos:
            clave = (turno.paciente_id, turno.odontologo_id)

            if clave in pares_procesados:
                continue

            pares_procesados.add(clave)
            asociacion = asegurar_paciente_asociado_a_odontologo(
                turno.paciente,
                turno.odontologo,
                motivo="Reconstruida desde turnos existentes",
            )

            if asociacion:
                asociaciones += 1

        self.stdout.write(
            self.style.SUCCESS(f"Asociaciones reconstruidas o verificadas: {asociaciones}")
        )
