from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Prueba escritura, lectura y borrado del storage de adjuntos clinicos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conservar",
            action="store_true",
            help="No borra el archivo de prueba al finalizar.",
        )

    def handle(self, *args, **options):
        contenido = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
            b"\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
            b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        nombre = f"pruebas/historias/storage_{uuid4().hex}.png"
        archivo = ContentFile(contenido)
        archivo.content_type = "image/png"

        try:
            guardado = default_storage.save(nombre, archivo)

            with default_storage.open(guardado, "rb") as archivo:
                contenido_leido = archivo.read()

            if contenido_leido != contenido:
                raise CommandError("El contenido leido no coincide con el contenido guardado.")

            try:
                url = default_storage.url(guardado)
            except Exception:
                url = ""

            self.stdout.write(self.style.SUCCESS("Storage de historias funcionando."))
            self.stdout.write(f"Archivo de prueba: {guardado}")

            if url:
                self.stdout.write(f"URL temporal o local: {url}")

            if not options["conservar"]:
                default_storage.delete(guardado)
                self.stdout.write("Archivo de prueba eliminado.")
        except Exception as error:
            raise CommandError(f"No se pudo probar el storage de historias: {error}") from error
