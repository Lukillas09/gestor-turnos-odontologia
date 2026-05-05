from django.urls import reverse
from django.test import TestCase

from .models import Paciente


class PacienteViewsTests(TestCase):
    def test_listado_muestra_pacientes(self):
        Paciente.objects.create(
            nombre="Ana",
            apellido="Gomez",
            documento="12345678",
            telefono="1122334455",
            email="ana@example.com",
        )

        response = self.client.get(reverse("pacientes:lista"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gomez")
        self.assertContains(response, "12345678")

    def test_listado_filtra_por_busqueda(self):
        Paciente.objects.create(nombre="Ana", apellido="Gomez", documento="12345678")
        Paciente.objects.create(nombre="Luis", apellido="Perez", documento="87654321")

        response = self.client.get(reverse("pacientes:lista"), {"q": "Perez"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Perez")
        self.assertNotContains(response, "Gomez")

    def test_creacion_de_paciente_valido(self):
        response = self.client.post(
            reverse("pacientes:crear"),
            {
                "nombre": "Carla",
                "apellido": "Lopez",
                "documento": "30111222",
                "telefono": "1155667788",
                "email": "carla@example.com",
                "fecha_nacimiento": "",
                "observaciones": "",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertTrue(Paciente.objects.filter(documento="30111222").exists())

    def test_creacion_permite_mas_de_un_paciente_sin_documento(self):
        datos_base = {
            "telefono": "",
            "email": "",
            "fecha_nacimiento": "",
            "observaciones": "",
        }

        primera_respuesta = self.client.post(
            reverse("pacientes:crear"),
            {
                **datos_base,
                "nombre": "Mario",
                "apellido": "Sosa",
                "documento": "",
            },
        )
        segunda_respuesta = self.client.post(
            reverse("pacientes:crear"),
            {
                **datos_base,
                "nombre": "Laura",
                "apellido": "Diaz",
                "documento": "",
            },
        )

        self.assertRedirects(primera_respuesta, reverse("pacientes:lista"))
        self.assertRedirects(segunda_respuesta, reverse("pacientes:lista"))
        self.assertEqual(Paciente.objects.filter(documento__isnull=True).count(), 2)

# Create your tests here.
