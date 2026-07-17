from django.core.management.base import BaseCommand, CommandError

from historias.access_policy import registrar_evento_acceso_clinico
from historias.models import AccesoClinicoAuditoria, HistoriaClinica
from historias.services import verificar_integridad_historia


class Command(BaseCommand):
    help = "Verifica sellos encadenados y, opcionalmente, adjuntos clínicos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--paciente",
            type=int,
            help="Limita la verificación a las historias de un paciente.",
        )
        parser.add_argument(
            "--historia",
            type=int,
            help="Limita la verificación a una historia clínica.",
        )
        parser.add_argument(
            "--verificar-adjuntos",
            action="store_true",
            help="Lee cada archivo para comparar su SHA-256.",
        )
        parser.add_argument(
            "--fallar-si-hay-errores",
            action="store_true",
            help="Finaliza con error si se detecta una inconsistencia.",
        )

    def handle(self, *args, **options):
        queryset = HistoriaClinica.objects.select_related("paciente").order_by("pk")
        if options["paciente"]:
            queryset = queryset.filter(paciente_id=options["paciente"])
        if options["historia"]:
            queryset = queryset.filter(pk=options["historia"])

        verificadas = 0
        versiones_verificadas = 0
        enmiendas_verificadas = 0
        errores_integridad = 0
        for historia in queryset.iterator():
            try:
                resultado = verificar_integridad_historia(
                    historia,
                    verificar_adjuntos=options["verificar_adjuntos"],
                )
                valida = resultado["valida"]
                tipos_error = resultado["tipos_error"]
                versiones_verificadas += resultado["versiones_verificadas"]
                enmiendas_verificadas += resultado["enmiendas_verificadas"]
                errores_integridad += len(resultado["errores"])
            except Exception:
                valida = False
                tipos_error = ["VERIFICACION_NO_COMPLETADA"]
                errores_integridad += 1

            verificadas += 1
            registrar_evento_acceso_clinico(
                usuario=None,
                accion=AccesoClinicoAuditoria.Accion.VERIFICAR_INTEGRIDAD,
                resultado=(
                    AccesoClinicoAuditoria.Resultado.PERMITIDO
                    if valida
                    else AccesoClinicoAuditoria.Resultado.ERROR
                ),
                politica=AccesoClinicoAuditoria.Politica.SISTEMA,
                paciente=historia.paciente,
                historia=historia,
                motivo="Verificación de integridad ejecutada por comando.",
            )
            estilo = self.style.SUCCESS if valida else self.style.ERROR
            estado = "válida" if valida else "inválida"
            detalle = "" if valida else f" Tipos: {', '.join(tipos_error)}."
            self.stdout.write(estilo(f"Historia {historia.pk}: {estado}.{detalle}"))

        self.stdout.write(f"Historias verificadas: {verificadas}")
        self.stdout.write(f"Versiones verificadas: {versiones_verificadas}")
        self.stdout.write(f"Enmiendas verificadas: {enmiendas_verificadas}")
        self.stdout.write(f"Errores de integridad: {errores_integridad}")
        if errores_integridad and options["fallar_si_hay_errores"]:
            raise CommandError("Se detectaron historias clínicas con integridad inválida.")
