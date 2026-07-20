from datetime import time

from indicaciones.forms import IndicacionBorradorForm

from .base import IndicacionesTestCase


class IndicacionBorradorFormRegressionTests(IndicacionesTestCase):
    def setUp(self):
        super().setUp()
        self.turno = self.crear_turno()
        self.historia = self.crear_historia()
        self.turno_alternativo = self.crear_turno(
            hora_inicio=time(10, 0),
            motivo="Consulta alternativa ficticia",
        )
        self.historia_alternativa = self.crear_historia(
            motivo="Historia alternativa ficticia para la prueba.",
        )
        self.turno_otro_paciente = self.crear_turno(
            paciente=self.paciente_fuera_de_alcance,
            hora_inicio=time(11, 0),
        )
        self.historia_otro_paciente = self.crear_historia(
            paciente=self.paciente_fuera_de_alcance,
        )
        self.turno_otro_odontologo = self.crear_turno(
            odontologo=self.otro_odontologo,
        )
        self.historia_otro_odontologo = self.crear_historia(
            odontologo=self.otro_odontologo,
        )

    def datos_formulario(self, *, turno=None, historia=None):
        return {
            "plantilla": "",
            "turno": str(turno.pk) if turno else "",
            "historia_clinica": str(historia.pk) if historia else "",
            "titulo": "Indicaciones de prueba",
            "procedimiento": "Procedimiento de prueba",
            "contenido": "Contenido clinico ficticio definido por el profesional.",
            "pautas_alarma": "",
            "recomendaciones_control": "",
            "observaciones_personalizadas": "",
            "proximo_control_en": "",
        }

    def crear_formulario(self, *, turno=None, historia=None, **kwargs):
        return IndicacionBorradorForm(
            data=self.datos_formulario(turno=turno, historia=historia),
            paciente=self.paciente,
            odontologo=self.odontologo,
            **kwargs,
        )

    def assert_creacion_valida(self, *, turno=None, historia=None):
        form = self.crear_formulario(turno=turno, historia=historia)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.paciente, self.paciente)
        self.assertEqual(form.instance.odontologo, self.odontologo)
        self.assertEqual(form.cleaned_data["turno"], turno)
        self.assertEqual(form.cleaned_data["historia_clinica"], historia)
        self.assertNotIn("paciente", form.fields)
        self.assertNotIn("odontologo", form.fields)

    def test_creacion_sin_turno_ni_historia_es_valida(self):
        self.assert_creacion_valida()

    def test_creacion_solamente_con_turno_correcto_es_valida(self):
        self.assert_creacion_valida(turno=self.turno)

    def test_creacion_solamente_con_historia_correcta_es_valida(self):
        self.assert_creacion_valida(historia=self.historia)

    def test_creacion_con_turno_e_historia_del_contexto_es_valida(self):
        self.assert_creacion_valida(
            turno=self.turno,
            historia=self.historia,
        )

    def test_turno_de_otro_paciente_es_invalid_choice(self):
        form = self.crear_formulario(turno=self.turno_otro_paciente)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["turno"][0].code, "invalid_choice")

    def test_historia_de_otro_paciente_es_invalid_choice(self):
        form = self.crear_formulario(historia=self.historia_otro_paciente)

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors.as_data()["historia_clinica"][0].code,
            "invalid_choice",
        )

    def test_turno_del_mismo_paciente_y_otro_odontologo_es_invalid_choice(self):
        form = self.crear_formulario(turno=self.turno_otro_odontologo)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["turno"][0].code, "invalid_choice")

    def test_historia_del_mismo_paciente_y_otro_odontologo_es_invalid_choice(self):
        form = self.crear_formulario(historia=self.historia_otro_odontologo)

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors.as_data()["historia_clinica"][0].code,
            "invalid_choice",
        )

    def test_ids_manipulados_fuera_del_queryset_son_invalid_choice(self):
        datos = self.datos_formulario()
        datos["turno"] = "999999"
        datos["historia_clinica"] = "999999"
        form = IndicacionBorradorForm(
            data=datos,
            paciente=self.paciente,
            odontologo=self.odontologo,
        )

        self.assertFalse(form.is_valid())
        errores = form.errors.as_data()
        self.assertEqual(errores["turno"][0].code, "invalid_choice")
        self.assertEqual(errores["historia_clinica"][0].code, "invalid_choice")

    def test_edicion_con_contexto_y_relaciones_actuales_es_valida(self):
        borrador = self.crear_borrador(
            turno=self.turno,
            historia_clinica=self.historia,
        )
        form = self.crear_formulario(
            turno=self.turno,
            historia=self.historia,
            instance=borrador,
            permitir_plantilla=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.paciente_id, self.paciente.pk)
        self.assertEqual(form.instance.odontologo_id, self.odontologo.pk)

    def test_edicion_admite_otras_relaciones_del_mismo_contexto(self):
        borrador = self.crear_borrador(
            turno=self.turno,
            historia_clinica=self.historia,
        )
        form = self.crear_formulario(
            turno=self.turno_alternativo,
            historia=self.historia_alternativa,
            instance=borrador,
            permitir_plantilla=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["turno"], self.turno_alternativo)
        self.assertEqual(
            form.cleaned_data["historia_clinica"],
            self.historia_alternativa,
        )
        self.assertEqual(form.instance.paciente_id, self.paciente.pk)
        self.assertEqual(form.instance.odontologo_id, self.odontologo.pk)

    def test_edicion_con_paciente_contextual_incorrecto_falla_explicito(self):
        borrador = self.crear_borrador()

        with self.assertRaisesMessage(ValueError, "paciente indicado"):
            IndicacionBorradorForm(
                data=self.datos_formulario(),
                instance=borrador,
                paciente=self.paciente_fuera_de_alcance,
                odontologo=self.odontologo,
                permitir_plantilla=False,
            )

        self.assertEqual(borrador.paciente_id, self.paciente.pk)

    def test_edicion_con_odontologo_contextual_incorrecto_falla_explicito(self):
        borrador = self.crear_borrador()

        with self.assertRaisesMessage(ValueError, "odontólogo indicado"):
            IndicacionBorradorForm(
                data=self.datos_formulario(),
                instance=borrador,
                paciente=self.paciente,
                odontologo=self.otro_odontologo,
                permitir_plantilla=False,
            )

        self.assertEqual(borrador.odontologo_id, self.odontologo.pk)
