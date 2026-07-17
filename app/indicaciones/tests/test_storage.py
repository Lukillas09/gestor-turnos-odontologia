import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from config.storage_backends import PrivateClinicalFileSystemStorage


class PrivateClinicalStorageTests(SimpleTestCase):
    def test_no_expone_url_publica_y_usa_raiz_independiente(self):
        with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as privado:
            with override_settings(MEDIA_ROOT=media, PRIVATE_CLINICAL_ROOT=Path(privado)):
                storage = PrivateClinicalFileSystemStorage()

                self.assertEqual(Path(storage.location), Path(privado))
                self.assertNotEqual(Path(storage.location), Path(media))
                with self.assertRaisesMessage(ValueError, "no tienen una URL pública"):
                    storage.url("indicaciones/uuid/documento.pdf")
