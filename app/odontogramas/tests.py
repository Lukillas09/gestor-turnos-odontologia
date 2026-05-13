import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from pacientes.models import Paciente, PacienteOdontologo
from turnos.models import Odontologo
from usuarios.roles import ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .domain import color_para_estado
from .models import EstadoDental, Odontograma


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


class OdontogramaTests(TestCase):
    def setUp(self):
        self.paciente = Paciente.objects.create(
            nombre="Juan",
            apellido="Odonto",
            documento="55111222",
        )
        self.usuario_odontologo = get_user_model().objects.create_user(
            username="dr.odontograma",
            password="Password123!",
        )
        asignar_rol(self.usuario_odontologo, ROL_ODONTOLOGO)
        self.odontologo = Odontologo.objects.create(
            usuario=self.usuario_odontologo,
            matricula="MN-ODONTO",
        )
        PacienteOdontologo.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            motivo="Atención principal",
        )

    def test_detalle_crea_odontograma_automaticamente(self):
        self.client.force_login(self.usuario_odontologo)

        response = self.client.get(
            reverse("odontogramas:detalle_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Odontograma.objects.filter(paciente=self.paciente).exists())
        self.assertContains(response, "Odontograma clínico")
        self.assertContains(response, 'data-tooth="16"')
        self.assertContains(response, "Leyenda clínica")

    def test_odontologo_asociado_guarda_estado_dental_con_color_automatico(self):
        self.client.force_login(self.usuario_odontologo)

        response = self.client.post(
            reverse("odontogramas:crear_estado", kwargs={"paciente_pk": self.paciente.pk}),
            data=json.dumps(
                {
                    "diente": 16,
                    "cara": EstadoDental.CaraDental.OCLUSAL_INCISAL,
                    "estado_clinico": EstadoDental.EstadoClinico.CARIES,
                    "observacion": "Caries activa en oclusal.",
                    "realizado": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        estado = EstadoDental.objects.get(odontograma__paciente=self.paciente)
        self.assertEqual(estado.diente, 16)
        self.assertEqual(estado.cara, EstadoDental.CaraDental.OCLUSAL_INCISAL)
        self.assertEqual(estado.color, color_para_estado(EstadoDental.EstadoClinico.CARIES))
        self.assertEqual(estado.odontologo, self.odontologo)
        self.assertFalse(estado.realizado)
        self.assertEqual(data["estado"]["color"], "rojo")

    def test_actualizar_misma_cara_conserva_historial_y_un_solo_activo(self):
        odontograma = Odontograma.objects.create(paciente=self.paciente)
        EstadoDental.objects.create(
            odontograma=odontograma,
            diente=16,
            cara=EstadoDental.CaraDental.OCLUSAL_INCISAL,
            estado_clinico=EstadoDental.EstadoClinico.CARIES,
            color="rojo",
            odontologo=self.odontologo,
            registrado_por=self.usuario_odontologo,
            activo=True,
        )
        self.client.force_login(self.usuario_odontologo)

        response = self.client.post(
            reverse("odontogramas:crear_estado", kwargs={"paciente_pk": self.paciente.pk}),
            data=json.dumps(
                {
                    "diente": 16,
                    "cara": EstadoDental.CaraDental.OCLUSAL_INCISAL,
                    "estado_clinico": EstadoDental.EstadoClinico.OBTURACION,
                    "observacion": "Obturación realizada.",
                    "realizado": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        estados = EstadoDental.objects.filter(
            odontograma=odontograma,
            diente=16,
            cara=EstadoDental.CaraDental.OCLUSAL_INCISAL,
        )
        self.assertEqual(estados.count(), 2)
        self.assertEqual(estados.filter(activo=True).count(), 1)
        self.assertEqual(estados.get(activo=True).estado_clinico, "obturacion")

    def test_recepcionista_visualiza_pero_no_edita(self):
        usuario = get_user_model().objects.create_user(username="recepcion.odonto")
        asignar_rol(usuario, ROL_RECEPCIONISTA)
        self.client.force_login(usuario)

        response_get = self.client.get(
            reverse("odontogramas:detalle_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )
        response_post = self.client.post(
            reverse("odontogramas:crear_estado", kwargs={"paciente_pk": self.paciente.pk}),
            data=json.dumps(
                {
                    "diente": 16,
                    "cara": EstadoDental.CaraDental.OCLUSAL_INCISAL,
                    "estado_clinico": EstadoDental.EstadoClinico.CARIES,
                    "observacion": "",
                    "realizado": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, "Solo lectura")
        self.assertEqual(response_post.status_code, 403)

    def test_odontologo_no_asociado_visualiza_pero_no_edita(self):
        usuario = get_user_model().objects.create_user(username="dr.no.asociado")
        asignar_rol(usuario, ROL_ODONTOLOGO)
        Odontologo.objects.create(usuario=usuario, matricula="MN-NO-ASOC")
        self.client.force_login(usuario)

        response_get = self.client.get(
            reverse("odontogramas:detalle_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )
        response_post = self.client.post(
            reverse("odontogramas:crear_estado", kwargs={"paciente_pk": self.paciente.pk}),
            data=json.dumps(
                {
                    "diente": 16,
                    "cara": EstadoDental.CaraDental.OCLUSAL_INCISAL,
                    "estado_clinico": EstadoDental.EstadoClinico.CARIES,
                    "observacion": "",
                    "realizado": False,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response_get.status_code, 200)
        self.assertEqual(response_post.status_code, 403)

    def test_acceso_requiere_login(self):
        response = self.client.get(
            reverse("odontogramas:detalle_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('odontogramas:detalle_paciente', kwargs={'paciente_pk': self.paciente.pk})}",
        )
