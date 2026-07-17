from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings

from indicaciones.admin import (
    IndicacionPacienteAdmin,
    PlantillaIndicacionAdmin,
    PlantillaIndicacionVersionAdmin,
)
from indicaciones.models import IndicacionPaciente, PlantillaIndicacion, PlantillaIndicacionVersion

from .base import IndicacionesTestCase


class IndicacionesAdminTests(IndicacionesTestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def _request(self, usuario):
        request = self.factory.get("/admin/indicaciones/")
        request.user = usuario
        request.session = {}
        return request

    def test_indicaciones_son_readonly_sin_borrado_y_limitadas_por_alcance(self):
        propia = self.crear_borrador()
        ajena = IndicacionPaciente.objects.create(
            paciente=self.paciente_fuera_de_alcance,
            odontologo=self.otro_odontologo,
            titulo="Indicación ajena ficticia",
            contenido="Contenido ficticio fuera de alcance.",
            creado_por=self.otro_usuario,
            actualizado_por=self.otro_usuario,
        )
        modelo_admin = IndicacionPacienteAdmin(IndicacionPaciente, admin.site)
        request = self._request(self.usuario)

        ids = set(modelo_admin.get_queryset(request).values_list("pk", flat=True))

        self.assertEqual(ids, {propia.pk})
        self.assertNotIn(ajena.pk, ids)
        self.assertFalse(modelo_admin.has_add_permission(request))
        self.assertFalse(modelo_admin.has_change_permission(request, propia))
        self.assertFalse(modelo_admin.has_delete_permission(request, propia))
        self.assertIn("contenido", modelo_admin.get_readonly_fields(request, propia))

    def test_superusuario_sin_identidad_profesional_no_obtiene_acceso_clinico(self):
        superusuario = get_user_model().objects.create_superuser(
            username="admin-sin-identidad-clinica",
            password="clave-pruebas",
            email="admin@example.test",
        )
        request = self._request(superusuario)

        indicaciones_admin = IndicacionPacienteAdmin(IndicacionPaciente, admin.site)
        plantillas_admin = PlantillaIndicacionAdmin(PlantillaIndicacion, admin.site)

        self.assertFalse(indicaciones_admin.has_module_permission(request))
        self.assertFalse(plantillas_admin.has_module_permission(request))

    def test_versiones_de_plantilla_son_solo_lectura(self):
        request = self._request(self.usuario)
        modelo_admin = PlantillaIndicacionVersionAdmin(
            PlantillaIndicacionVersion,
            admin.site,
        )

        self.assertTrue(modelo_admin.has_view_permission(request))
        self.assertFalse(modelo_admin.has_add_permission(request))
        self.assertFalse(modelo_admin.has_change_permission(request))
        self.assertFalse(modelo_admin.has_delete_permission(request))

    @override_settings(INDICACIONES_POSTOPERATORIAS_ENABLED=False)
    def test_feature_flag_oculta_todos_los_modelos_del_admin(self):
        request = self._request(self.usuario)

        administradores = (
            PlantillaIndicacionAdmin(PlantillaIndicacion, admin.site),
            PlantillaIndicacionVersionAdmin(PlantillaIndicacionVersion, admin.site),
            IndicacionPacienteAdmin(IndicacionPaciente, admin.site),
        )

        for modelo_admin in administradores:
            with self.subTest(modelo=modelo_admin.model.__name__):
                self.assertFalse(modelo_admin.has_module_permission(request))
                self.assertFalse(modelo_admin.has_view_permission(request))
