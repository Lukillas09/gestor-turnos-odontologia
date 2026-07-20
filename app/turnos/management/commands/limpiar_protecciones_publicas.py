from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from turnos.models import IdempotenciaSolicitudPublica, LimitePublico


class Command(BaseCommand):
    help = "Elimina por lotes rate limits e idempotencias públicas expiradas."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--max-batches", type=int, default=20)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        max_batches = options["max_batches"]
        dry_run = options["dry_run"]

        if batch_size <= 0:
            raise CommandError("--batch-size debe ser mayor a cero.")

        if max_batches <= 0:
            raise CommandError("--max-batches debe ser mayor a cero.")

        ahora = timezone.now()
        modelos = (
            ("límites", LimitePublico),
            ("idempotencias", IdempotenciaSolicitudPublica),
        )
        resultados = {}

        for nombre, modelo in modelos:
            if dry_run:
                cantidad = modelo.objects.filter(expira_en__lt=ahora).count()
                resultados[nombre] = min(cantidad, batch_size * max_batches)
            else:
                resultados[nombre] = self._eliminar_por_lotes(
                    modelo,
                    ahora=ahora,
                    batch_size=batch_size,
                    max_batches=max_batches,
                )

        prefijo = "Simulación" if dry_run else "Resultado"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefijo}: límites={resultados['límites']}; "
                f"idempotencias={resultados['idempotencias']}."
            )
        )

    @staticmethod
    def _eliminar_por_lotes(modelo, *, ahora, batch_size, max_batches):
        eliminados = 0

        for _numero_lote in range(max_batches):
            with transaction.atomic():
                ids = list(
                    modelo.objects.filter(expira_en__lt=ahora)
                    .order_by("expira_en", "pk")
                    .values_list("pk", flat=True)[:batch_size]
                )

                if not ids:
                    break

                cantidad, _detalle = modelo.objects.filter(
                    pk__in=ids,
                    expira_en__lt=ahora,
                ).delete()
                eliminados += cantidad

        return eliminados
