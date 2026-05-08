from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.test import TestCase
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .models import Paciente


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


class LoginInternoTests(TestCase):
    def test_login_responde_correctamente(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresar")

    def test_login_redirige_a_pacientes_con_credenciales_validas(self):
        usuario = get_user_model().objects.create_user(
            username="recepcion",
            password="Password123!",
        )
        asignar_rol(usuario, ROL_RECEPCIONISTA)

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

    def test_borrado_requiere_login(self):
        url = reverse("pacientes:borrar", kwargs={"pk": 1})

        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_odontologo_puede_ver_pacientes_sin_crear_ni_editar(self):
        usuario = get_user_model().objects.create_user(username="dr.pacientes")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-PAC")
        paciente = Paciente.objects.create(nombre="Eva", apellido="Ramos", documento="10111222")
        self.client.force_login(usuario)

        response_lista = self.client.get(reverse("pacientes:lista"))
        response_crear = self.client.get(reverse("pacientes:crear"))
        response_editar = self.client.get(reverse("pacientes:editar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response_lista.status_code, 200)
        self.assertNotContains(response_lista, "Nuevo paciente")
        self.assertEqual(response_crear.status_code, 403)
        self.assertEqual(response_editar.status_code, 403)


class PacienteViewsTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username="usuario.pacientes",
            password="Password123!",
        )
        asignar_rol(self.usuario, ROL_RECEPCIONISTA)
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

    def test_borrado_muestra_confirmacion_segura(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.get(reverse("pacientes:borrar", kwargs={"pk": paciente.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Borrar paciente")
        self.assertContains(response, "Confirmacion segura")
        self.assertContains(response, "40111222")

    def test_borrado_rechaza_datos_que_no_coinciden(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Clara",
                "apellido": "Otro",
                "documento": "40111222",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "Los datos ingresados no coinciden")

    def test_borrado_elimina_paciente_con_confirmacion_correcta(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Borrar",
            documento="40111222",
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "clara",
                "apellido": "borrar",
                "documento": "40111222",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertFalse(Paciente.objects.filter(pk=paciente.pk).exists())

    def test_borrado_no_elimina_paciente_con_turno_pendiente(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Protegida",
            documento="42111222",
        )
        self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.PENDIENTE,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Clara",
                "apellido": "Protegida",
                "documento": "42111222",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "tiene turnos pendientes o confirmados")

    def test_borrado_no_elimina_paciente_con_turno_confirmado(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Confirmada",
            documento="45111222",
        )
        self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.CONFIRMADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Clara",
                "apellido": "Confirmada",
                "documento": "45111222",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertContains(response, "tiene turnos pendientes o confirmados")

    def test_borrado_elimina_paciente_con_turnos_cancelados(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Cancelada",
            documento="44111222",
        )
        turno = self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.CANCELADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Clara",
                "apellido": "Cancelada",
                "documento": "44111222",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertFalse(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertFalse(Turno.objects.filter(pk=turno.pk).exists())

    def test_borrado_elimina_paciente_con_turnos_realizados(self):
        paciente = Paciente.objects.create(
            nombre="Clara",
            apellido="Realizada",
            documento="46111222",
        )
        turno = self._crear_turno_para_paciente(
            paciente=paciente,
            estado=Turno.Estado.REALIZADO,
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": paciente.pk}),
            {
                "nombre": "Clara",
                "apellido": "Realizada",
                "documento": "46111222",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertFalse(Paciente.objects.filter(pk=paciente.pk).exists())
        self.assertFalse(Turno.objects.filter(pk=turno.pk).exists())

    def _crear_turno_para_paciente(self, paciente, estado):
        usuario_odontologo = get_user_model().objects.create_user(
            username=f"dr.{paciente.documento}"
        )
        odontologo = Odontologo.objects.create(
            usuario=usuario_odontologo,
            matricula=f"MN-{paciente.documento}",
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=DisponibilidadOdontologo.DiaSemana.VIERNES,
            hora_inicio=time(9, 0),
            hora_fin=time(18, 0),
        )
        return Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=date(2026, 5, 8),
            hora_inicio=time(10, 0),
            duracion_minutos=30,
            estado=estado,
        )


class PacienteDeleteRoleTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombre="Rol",
            apellido="Delete",
            documento="43111222",
        )

    def test_odontologo_puede_borrar_paciente_con_confirmacion(self):
        usuario = get_user_model().objects.create_user(username="dr.delete")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-DEL-ODO")
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": self.paciente.pk}),
            {
                "nombre": "Rol",
                "apellido": "Delete",
                "documento": "43111222",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertFalse(Paciente.objects.filter(pk=self.paciente.pk).exists())

    def test_administrador_puede_borrar_paciente_con_confirmacion(self):
        usuario = get_user_model().objects.create_user(username="admin.delete", is_staff=True)
        asignar_rol(usuario, ROL_ADMINISTRADOR)
        self.client.force_login(usuario)

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": self.paciente.pk}),
            {
                "nombre": "Rol",
                "apellido": "Delete",
                "documento": "43111222",
            },
        )

        self.assertRedirects(response, reverse("pacientes:lista"))
        self.assertFalse(Paciente.objects.filter(pk=self.paciente.pk).exists())
