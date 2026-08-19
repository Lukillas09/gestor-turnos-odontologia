from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.utils import timezone

from turnos.models import Turno
from turnos.notifications import (
    notificar_solicitud_turno_recibida,
    notificar_turno_cancelado,
    notificar_turno_confirmado,
)


@dataclass(frozen=True)
class PacienteEmailPrueba:
    nombre: str
    apellido: str
    email: str

    @property
    def nombre_completo(self):
        return f"{self.apellido}, {self.nombre}"


@dataclass(frozen=True)
class OdontologoEmailPrueba:
    nombre_completo: str


@dataclass
class TurnoEmailPrueba:
    paciente: PacienteEmailPrueba
    odontologo: OdontologoEmailPrueba
    fecha: object
    hora_inicio: time
    duracion_minutos: int
    estado: str
    motivo: str

    @property
    def hora_fin(self):
        fecha_hora_fin = datetime.combine(self.fecha, self.hora_inicio) + timedelta(
            minutes=self.duracion_minutos
        )
        return fecha_hora_fin.time()

    @property
    def hora_fin_atencion(self):
        return self.hora_fin

    def get_estado_display(self):
        return dict(Turno.Estado.choices)[self.estado]


class Command(BaseCommand):
    help = "Envia las 3 notificaciones de turno a un email de prueba."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="Email que recibira las notificaciones.")

    def handle(self, *args, **options):
        destinatario = options["destinatario"].strip()

        try:
            validate_email(destinatario)
        except ValidationError as exc:
            raise CommandError("El destinatario no es un email valido.") from exc

        turno = TurnoEmailPrueba(
            paciente=PacienteEmailPrueba(
                nombre="Lucas",
                apellido="Martinez",
                email=destinatario,
            ),
            odontologo=OdontologoEmailPrueba(nombre_completo="Dra. Sofia Perez"),
            fecha=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
            motivo="Turno de prueba",
        )

        pruebas = [
            (
                Turno.Estado.PENDIENTE,
                notificar_solicitud_turno_recibida,
                "solicitud recibida",
            ),
            (
                Turno.Estado.CONFIRMADO,
                notificar_turno_confirmado,
                "turno confirmado",
            ),
            (
                Turno.Estado.CANCELADO,
                notificar_turno_cancelado,
                "turno cancelado",
            ),
        ]

        for estado, notificador, nombre in pruebas:
            turno.estado = estado
            resultado = notificador(turno, fail_silently=False)

            if not resultado.enviada:
                raise CommandError(f"No se pudo enviar la notificacion de {nombre}.")

            self.stdout.write(self.style.SUCCESS(f"Enviada notificacion de {nombre}."))

        self.stdout.write(self.style.SUCCESS(f"Se enviaron 3 notificaciones a {destinatario}."))
