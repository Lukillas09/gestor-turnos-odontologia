from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email


class Command(BaseCommand):
    help = "Envía un email de prueba usando la configuración EMAIL_* actual."

    def add_arguments(self, parser):
        parser.add_argument("destinatario", help="Email que recibira el mensaje de prueba.")
        parser.add_argument(
            "--asunto",
            default="Email de prueba - Gestor de Turnos",
            help="Asunto del email de prueba.",
        )

    def handle(self, *args, **options):
        destinatario = options["destinatario"].strip()
        asunto = options["asunto"].strip()

        try:
            validate_email(destinatario)
        except ValidationError as exc:
            raise CommandError("El destinatario no es un email valido.") from exc

        enviados = send_mail(
            subject=asunto,
            message=(
                "Este es un email de prueba del Gestor de Turnos.\n\n"
                "Si recibiste este mensaje, la configuración de email está funcionando.\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[destinatario],
            fail_silently=False,
        )

        if enviados <= 0:
            raise CommandError("El proveedor no confirmo el envio del email.")

        self.stdout.write(self.style.SUCCESS(f"Email de prueba enviado a {destinatario}."))
