from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse
from django.test import TestCase
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import Odontologo
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .models import HistoriaClinica


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


def crear_odontologo(username="dr.historia", matricula="MN-HIST"):
    usuario = get_user_model().objects.create_user(username=username)
    asignar_rol(usuario, ROL_ODONTOLOGO)
    odontologo = Odontologo.objects.create(usuario=usuario, matricula=matricula)
    return usuario, odontologo


class HistoriaClinicaAccessTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombre="Ana",
            apellido="Clinica",
            documento="55111222",
        )
        self.usuario_odontologo, self.odontologo = crear_odontologo()

    def test_listado_requiere_login(self):
        url = reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})

        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_recepcionista_no_puede_acceder_a_historia_clinica(self):
        usuario = get_user_model().objects.create_user(username="recepcion.historia")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_administrador_no_puede_acceder_a_historia_clinica(self):
        usuario = get_user_model().objects.create_user(
            username="admin.historia",
            is_staff=True,
        )
        asignar_rol(usuario, ROL_ADMINISTRADOR)
        self.client.force_login(usuario)

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_odontologo_ve_boton_de_historia_en_detalle_de_paciente(self):
        self.client.force_login(self.usuario_odontologo)

        response = self.client.get(
            reverse("pacientes:detalle", kwargs={"pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historia clinica")

    def test_recepcionista_no_ve_boton_de_historia_en_detalle_de_paciente(self):
        usuario = get_user_model().objects.create_user(username="recepcion.sin.historia")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)

        response = self.client.get(
            reverse("pacientes:detalle", kwargs={"pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Historia clinica")


class HistoriaClinicaViewsTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombre="Lucas",
            apellido="Paciente",
            documento="56111222",
        )
        self.usuario_odontologo, self.odontologo = crear_odontologo(
            username="dr.responsable",
            matricula="MN-RESP",
        )
        self.client.force_login(self.usuario_odontologo)

    def test_odontologo_crea_historia_clinica(self):
        fecha = timezone.localdate()
        proximo_control = fecha + timedelta(days=30)

        response = self.client.post(
            reverse("historias:crear", kwargs={"paciente_pk": self.paciente.pk}),
            {
                "fecha": fecha.isoformat(),
                "motivo_consulta": "Dolor molar",
                "diagnostico": "Caries",
                "tratamiento_realizado": "Restauracion",
                "pieza_dental": "16",
                "observaciones": "Paciente tolera bien el procedimiento.",
                "proximo_control": proximo_control.isoformat(),
            },
        )

        historia = HistoriaClinica.objects.get()

        self.assertRedirects(response, reverse("historias:detalle", kwargs={"pk": historia.pk}))
        self.assertEqual(historia.paciente, self.paciente)
        self.assertEqual(historia.odontologo, self.odontologo)
        self.assertEqual(historia.motivo_consulta, "Dolor molar")
        self.assertEqual(historia.pieza_dental, "16")

    def test_listado_muestra_historia_clinica_del_paciente(self):
        HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control",
            diagnostico="Sin lesiones activas",
        )

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Control")

    def test_detalle_muestra_datos_clinicos(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Consulta inicial",
            diagnostico="Gingivitis",
            tratamiento_realizado="Profilaxis",
        )

        response = self.client.get(reverse("historias:detalle", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consulta inicial")
        self.assertContains(response, "Gingivitis")
        self.assertContains(response, "Profilaxis")

    def test_odontologo_responsable_edita_historia_clinica(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control",
        )

        response = self.client.post(
            reverse("historias:editar", kwargs={"pk": historia.pk}),
            {
                "fecha": timezone.localdate().isoformat(),
                "motivo_consulta": "Control actualizado",
                "diagnostico": "Evolucion favorable",
                "tratamiento_realizado": "Pulido",
                "pieza_dental": "",
                "observaciones": "",
                "proximo_control": "",
            },
        )

        historia.refresh_from_db()

        self.assertRedirects(response, reverse("historias:detalle", kwargs={"pk": historia.pk}))
        self.assertEqual(historia.motivo_consulta, "Control actualizado")
        self.assertEqual(historia.diagnostico, "Evolucion favorable")

    def test_otro_odontologo_no_edita_historia_clinica(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control",
        )
        otro_usuario, _ = crear_odontologo(
            username="dr.otro",
            matricula="MN-OTRO",
        )
        self.client.force_login(otro_usuario)

        response = self.client.get(reverse("historias:editar", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 403)

    def test_no_permite_fecha_de_atencion_futura(self):
        fecha_futura = timezone.localdate() + timedelta(days=1)

        response = self.client.post(
            reverse("historias:crear", kwargs={"paciente_pk": self.paciente.pk}),
            {
                "fecha": fecha_futura.isoformat(),
                "motivo_consulta": "Control futuro",
                "diagnostico": "",
                "tratamiento_realizado": "",
                "pieza_dental": "",
                "observaciones": "",
                "proximo_control": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(HistoriaClinica.objects.exists())
        self.assertContains(response, "no puede ser futura")

    def test_no_borra_paciente_con_historia_clinica(self):
        HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control",
        )

        response = self.client.post(
            reverse("pacientes:borrar", kwargs={"pk": self.paciente.pk}),
            {
                "nombre": "Lucas",
                "apellido": "Paciente",
                "documento": "56111222",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Paciente.objects.filter(pk=self.paciente.pk).exists())
        self.assertContains(response, "tiene historia clinica cargada")
