import hashlib

from django.core.management.base import BaseCommand, CommandError

from historias.models import HistoriaClinicaAdjunto


class Command(BaseCommand):
    help = "Completa SHA-256 faltantes de adjuntos clínicos legacy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Informa adjuntos pendientes sin leer ni modificar archivos.",
        )
        parser.add_argument(
            "--historia",
            type=int,
            help="Limita el proceso a una historia clínica.",
        )
        parser.add_argument(
            "--fallar-si-hay-errores",
            action="store_true",
            help="Finaliza con error si algún adjunto no pudo procesarse.",
        )

    def handle(self, *args, **options):
        queryset = HistoriaClinicaAdjunto.objects.filter(sha256="").select_related("historia")
        if options["historia"]:
            queryset = queryset.filter(historia_id=options["historia"])
        adjuntos = list(queryset.order_by("pk"))

        if options["dry_run"]:
            self.stdout.write(f"Adjuntos con SHA-256 pendiente: {len(adjuntos)}.")
            return

        completados = 0
        errores = []
        for adjunto in adjuntos:
            if adjunto.historia.versiones.exists():
                errores.append(
                    f"Adjunto {adjunto.pk}: la historia ya tiene versiones y requiere revisión."
                )
                continue
            try:
                sha256 = self._calcular_sha256(adjunto)
                adjunto.sha256 = sha256
                adjunto.save(
                    permitir_backfill_sha256=True,
                    update_fields=["sha256"],
                )
                completados += 1
            except Exception:
                errores.append(f"Adjunto {adjunto.pk}: no se pudo leer el archivo.")

        self.stdout.write(
            self.style.SUCCESS(f"Hashes completados: {completados}. Errores: {len(errores)}.")
        )
        for error in errores:
            self.stderr.write(error)

        if errores and options["fallar_si_hay_errores"]:
            raise CommandError("No se pudieron completar todos los hashes de adjuntos.")

    @staticmethod
    def _calcular_sha256(adjunto):
        digest = hashlib.sha256()
        with adjunto.archivo.open("rb") as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                digest.update(bloque)
        return digest.hexdigest()
