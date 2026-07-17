from django.core.management.base import BaseCommand, CommandError

from historias.models import HistoriaClinica
from historias.services import inicializar_integridad_historia_legacy


class Command(BaseCommand):
    help = "Crea la versión inicial de integridad para historias clínicas legacy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Informa registros pendientes sin crear versiones.",
        )
        parser.add_argument(
            "--historia",
            type=int,
            help="Limita el proceso a una historia clínica.",
        )
        parser.add_argument(
            "--fallar-si-hay-errores",
            action="store_true",
            help="Finaliza con error si algún registro no pudo inicializarse.",
        )

    def handle(self, *args, **options):
        queryset = (
            HistoriaClinica.objects.filter(migrada_desde_legacy=True)
            .select_related("creado_por", "actualizado_por", "finalizada_por")
            .prefetch_related("versiones", "adjuntos")
            .order_by("paciente_id", "numero_asiento", "pk")
        )
        if options["historia"]:
            queryset = queryset.filter(pk=options["historia"])
        historias = [historia for historia in queryset if not historia.versiones.exists()]

        if options["dry_run"]:
            self.stdout.write(f"Historias legacy pendientes: {len(historias)}.")
            return

        creadas = 0
        errores = []
        for historia in historias:
            usuario = historia.finalizada_por or historia.actualizado_por or historia.creado_por
            if usuario is None:
                errores.append(
                    f"Historia {historia.pk}: no existe un usuario histórico verificable."
                )
                continue
            if any(not adjunto.sha256 for adjunto in historia.adjuntos.all()):
                errores.append(
                    f"Historia {historia.pk}: hay adjuntos sin SHA-256; "
                    "ejecutar el backfill primero."
                )
                continue
            try:
                _, creada = inicializar_integridad_historia_legacy(
                    historia=historia,
                    usuario=usuario,
                )
                creadas += int(creada)
            except Exception:
                errores.append(f"Historia {historia.pk}: no se pudo crear la versión inicial.")

        self.stdout.write(
            self.style.SUCCESS(f"Versiones legacy creadas: {creadas}. Errores: {len(errores)}.")
        )
        for error in errores:
            self.stderr.write(error)

        if errores and options["fallar_si_hay_errores"]:
            raise CommandError("No se inicializaron todas las historias clínicas legacy.")
