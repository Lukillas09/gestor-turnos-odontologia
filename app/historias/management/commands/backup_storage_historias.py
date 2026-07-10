import hashlib
import json
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from historias.models import HistoriaClinicaAdjunto


class Command(BaseCommand):
    help = "Crea un backup local de los adjuntos de historia clínica."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="../backups/storage",
            help="Directorio base donde guardar el backup.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra cuántos adjuntos se respaldarían sin descargar archivos.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"])
        dry_run = options["dry_run"]
        adjuntos = self._obtener_adjuntos()

        if dry_run:
            total_bytes = sum(adjunto.tamano_bytes for adjunto in adjuntos)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Adjuntos a respaldar: {len(adjuntos)}. "
                    f"Tamaño registrado: {total_bytes} bytes."
                )
            )
            return

        timestamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        backup_dir = output_dir / f"historias-storage-{timestamp}"
        archivos_dir = backup_dir / "archivos"
        archivos_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "generado_en": timezone.now().isoformat(),
            "storage_backend": default_storage.__class__.__module__
            + "."
            + default_storage.__class__.__name__,
            "total_adjuntos": len(adjuntos),
            "total_bytes": 0,
            "archivos": [],
        }

        try:
            for adjunto in adjuntos:
                manifest["archivos"].append(self._respaldar_adjunto(adjunto, archivos_dir))
        except Exception as error:
            raise CommandError(f"No se pudo crear el backup de Storage: {error}") from error

        manifest["total_bytes"] = sum(archivo["bytes"] for archivo in manifest["archivos"])
        self._guardar_manifest(backup_dir, manifest)

        self.stdout.write(
            self.style.SUCCESS(
                f"Backup de Storage creado en {backup_dir}. "
                f"Adjuntos: {manifest['total_adjuntos']}. "
                f"Bytes: {manifest['total_bytes']}."
            )
        )

    def _obtener_adjuntos(self):
        return list(
            HistoriaClinicaAdjunto.objects.select_related(
                "historia",
                "historia__paciente",
                "historia__odontologo",
            ).order_by("pk")
        )

    def _respaldar_adjunto(self, adjunto, archivos_dir):
        ruta_storage = adjunto.archivo.name
        ruta_destino_relativa = Path(str(adjunto.pk)) / Path(ruta_storage).name
        ruta_destino = archivos_dir / ruta_destino_relativa
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)

        sha256 = hashlib.sha256()
        bytes_escritos = 0

        with default_storage.open(ruta_storage, "rb") as origen:
            with ruta_destino.open("wb") as destino:
                for bloque in iter(lambda: origen.read(1024 * 1024), b""):
                    destino.write(bloque)
                    sha256.update(bloque)
                    bytes_escritos += len(bloque)

        return {
            "adjunto_id": adjunto.pk,
            "historia_id": adjunto.historia_id,
            "paciente_id": adjunto.historia.paciente_id,
            "odontologo_id": adjunto.historia.odontologo_id,
            "ruta_storage": ruta_storage,
            "ruta_backup": str(Path("archivos") / ruta_destino_relativa).replace("\\", "/"),
            "bytes": bytes_escritos,
            "sha256": sha256.hexdigest(),
        }

    @staticmethod
    def _guardar_manifest(backup_dir, manifest):
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
