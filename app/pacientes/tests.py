from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase

from .models import Paciente


class LoginInternoTests(TestCase):
    def test_login_responde_correctamente(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresar")

    def test_login_redirige_a_pacientes_con_credenciales_validas(self):
        get_user_model().objects.create_user(
            username="recepcion",
            password="Password123!",
        )

        response = self.client.post(
            reverse("login"),
            {
                "username": "recepcion",
                "password": "Password123!",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))

    def test_logout_redirige_al_login(self):
        usuario = get_user_model().objects.create_user(
            username="recepcion",
            password="Password123!",
        )
        self.client.force_login(usuario)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))


class PacienteAccessTests(TestCase):
    def test_listado_requiere_login(self):
        response = self.client.get(reverse("pacientes:lista"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('pacientes:lista')}")


class PacienteViewsTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username="usuario.pacientes",
            password="Password123!",
        )
        self.client.force_login(self.usuario)

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

    def test_detalle_muestra_datos_del_paciente(self):
        paciente = Paciente.objects.create(
            nombre="Elena",
            apellido="Rios",
            documento="20111222",
            telefono="1144556677",
            email="elena@example.com",
        )

        response = self.client.get(reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elena")
        self.assertContains(response, "20111222")

    def test_edicion_actualiza_paciente(self):
        paciente = Paciente.objects.create(
            nombre="Sofia",
            apellido="Mendez",
            documento="22111222",
            telefono="1111",
        )

        response = self.client.post(
            reverse("pacientes:editar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Sofia",
                "apellido": "Mendez",
                "documento": "22111222",
                "telefono": "2222",
                "email": "sofia@example.com",
                "fecha_nacimiento": "",
                "observaciones": "Paciente actualizada",
            },
        )

        paciente.refresh_from_db()

        self.assertRedirects(response, reverse("pacientes:detalle", kwargs={"pk": paciente.pk}))
        self.assertEqual(paciente.telefono, "2222")
        self.assertEqual(paciente.email, "sofia@example.com")
        self.assertEqual(paciente.observaciones, "Paciente actualizada")

    def test_edicion_no_permite_documento_duplicado(self):
        Paciente.objects.create(nombre="Ana", apellido="Gomez", documento="12345678")
        paciente = Paciente.objects.create(nombre="Luis", apellido="Perez", documento="87654321")

        response = self.client.post(
            reverse("pacientes:editar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Luis",
                "apellido": "Perez",
                "documento": "12345678",
                "telefono": "",
                "email": "",
                "fecha_nacimiento": "",
                "observaciones": "",
            },
        )

        paciente.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIn("documento", response.context["form"].errors)
        self.assertEqual(paciente.documento, "87654321")
