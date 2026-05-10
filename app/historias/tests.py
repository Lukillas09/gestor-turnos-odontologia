import tempfile
from datetime import date, time, timedelta
from io import StringIO
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.test import TestCase, override_settings
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import DisponibilidadOdontologo, Odontologo, Turno
from usuarios.roles import ROL_ADMINISTRADOR, ROL_ODONTOLOGO, ROL_RECEPCIONISTA

from .models import HistoriaClinica, HistoriaClinicaAdjunto


def asignar_rol(usuario, nombre_rol):
    grupo, _ = Group.objects.get_or_create(name=nombre_rol)
    usuario.groups.add(grupo)


def crear_odontologo(username="dr.historia", matricula="MN-HIST"):
    usuario = get_user_model().objects.create_user(username=username)
    asignar_rol(usuario, ROL_ODONTOLOGO)
    odontologo = Odontologo.objects.create(usuario=usuario, matricula=matricula)
    return usuario, odontologo


def crear_disponibilidad_laboral(odontologo, hora_inicio=time(9, 0), hora_fin=time(18, 0)):
    for dia_semana in range(5):
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=dia_semana,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
        )


def crear_turno_de_atencion(paciente, odontologo, fecha=date(2026, 5, 8)):
    crear_disponibilidad_laboral(odontologo)
    return Turno.objects.create(
        paciente=paciente,
        odontologo=odontologo,
        fecha=fecha,
        hora_inicio=time(9, 0),
        duracion_minutos=30,
        estado=Turno.Estado.REALIZADO,
    )


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
        crear_turno_de_atencion(self.paciente, self.odontologo)
        self.client.force_login(self.usuario_odontologo)

        response = self.client.get(
            reverse("pacientes:detalle", kwargs={"pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historia clinica")

    def test_odontologo_sin_turno_propio_no_ve_boton_de_historia(self):
        self.client.force_login(self.usuario_odontologo)

        response = self.client.get(
            reverse("pacientes:detalle", kwargs={"pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Historia clinica")

    def test_odontologo_sin_relacion_no_accede_a_historia_del_paciente(self):
        self.client.force_login(self.usuario_odontologo)

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk})
        )

        self.assertEqual(response.status_code, 403)

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
        self.media_dir = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_dir.name,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                },
            },
        )
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_dir.cleanup)
        self.paciente = Paciente.objects.create(
            nombre="Lucas",
            apellido="Paciente",
            documento="56111222",
        )
        self.usuario_odontologo, self.odontologo = crear_odontologo(
            username="dr.responsable",
            matricula="MN-RESP",
        )
        crear_turno_de_atencion(self.paciente, self.odontologo)
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
        self.assertEqual(historia.creado_por, self.usuario_odontologo)
        self.assertEqual(historia.actualizado_por, self.usuario_odontologo)

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

    def test_listado_filtra_por_busqueda_y_fecha(self):
        fecha_actual = timezone.localdate()
        fecha_anterior = fecha_actual - timedelta(days=10)
        HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=fecha_actual,
            motivo_consulta="Dolor molar",
            diagnostico="Caries profunda",
            pieza_dental="36",
        )
        HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=fecha_anterior,
            motivo_consulta="Control de rutina",
            diagnostico="Sin lesiones",
            pieza_dental="11",
        )

        response = self.client.get(
            reverse("historias:lista_paciente", kwargs={"paciente_pk": self.paciente.pk}),
            {
                "q": "caries",
                "fecha_desde": fecha_actual.isoformat(),
                "fecha_hasta": fecha_actual.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dolor molar")
        self.assertContains(response, "Caries profunda")
        self.assertNotContains(response, "Control de rutina")

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
        self.assertEqual(historia.actualizado_por, self.usuario_odontologo)

    def test_creacion_guarda_adjuntos_clinicos(self):
        fecha = timezone.localdate()
        archivo = SimpleUploadedFile(
            "radiografia.png",
            b"contenido-radiografia",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("historias:crear", kwargs={"paciente_pk": self.paciente.pk}),
            {
                "fecha": fecha.isoformat(),
                "motivo_consulta": "Control con radiografia",
                "diagnostico": "Evaluacion radiografica",
                "tratamiento_realizado": "",
                "pieza_dental": "36",
                "observaciones": "",
                "proximo_control": "",
                "adjuntos": [archivo],
            },
        )

        historia = HistoriaClinica.objects.get()
        adjunto = HistoriaClinicaAdjunto.objects.get()

        self.assertRedirects(response, reverse("historias:detalle", kwargs={"pk": historia.pk}))
        self.assertEqual(adjunto.historia, historia)
        self.assertEqual(adjunto.subido_por, self.usuario_odontologo)
        self.assertTrue(adjunto.nombre_archivo.endswith("radiografia.png"))

    def test_rechaza_adjunto_no_permitido(self):
        fecha = timezone.localdate()
        archivo = SimpleUploadedFile(
            "archivo.exe",
            b"contenido",
            content_type="application/octet-stream",
        )

        response = self.client.post(
            reverse("historias:crear", kwargs={"paciente_pk": self.paciente.pk}),
            {
                "fecha": fecha.isoformat(),
                "motivo_consulta": "Control con archivo invalido",
                "diagnostico": "",
                "tratamiento_realizado": "",
                "pieza_dental": "",
                "observaciones": "",
                "proximo_control": "",
                "adjuntos": [archivo],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(HistoriaClinica.objects.exists())
        self.assertContains(response, "PDF, imagen o DICOM")

    def test_detalle_muestra_adjuntos_y_auditoria(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            creado_por=self.usuario_odontologo,
            actualizado_por=self.usuario_odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Consulta con adjunto",
        )
        HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile("radiografia.pdf", b"pdf", content_type="application/pdf"),
            subido_por=self.usuario_odontologo,
        )

        response = self.client.get(reverse("historias:detalle", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adjuntos")
        self.assertContains(response, "radiografia.pdf")
        self.assertContains(response, self.usuario_odontologo.username)

    def test_otro_odontologo_sin_relacion_no_edita_historia_clinica(self):
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

        self.assertEqual(response.status_code, 404)

    def test_odontologo_relacionado_no_responsable_no_edita_historia_clinica(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control",
        )
        otro_usuario, otro_odontologo = crear_odontologo(
            username="dr.relacionado",
            matricula="MN-REL",
        )
        crear_turno_de_atencion(
            self.paciente,
            otro_odontologo,
            fecha=date(2026, 5, 11),
        )
        self.client.force_login(otro_usuario)

        response = self.client.get(reverse("historias:editar", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 403)

    def test_otro_odontologo_sin_relacion_no_ve_detalle_clinico(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Control privado",
        )
        otro_usuario, _ = crear_odontologo(
            username="dr.sin.relacion",
            matricula="MN-SIN-REL",
        )
        self.client.force_login(otro_usuario)

        response = self.client.get(reverse("historias:detalle", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 404)

    def test_odontologo_con_turno_propio_ve_historia_del_paciente(self):
        usuario_autor, odontologo_autor = crear_odontologo(
            username="dr.autor",
            matricula="MN-AUTOR",
        )
        crear_turno_de_atencion(
            self.paciente,
            odontologo_autor,
            fecha=date(2026, 5, 11),
        )
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=odontologo_autor,
            creado_por=usuario_autor,
            actualizado_por=usuario_autor,
            fecha=timezone.localdate(),
            motivo_consulta="Entrada compartida por paciente relacionado",
        )

        response = self.client.get(reverse("historias:detalle", kwargs={"pk": historia.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entrada compartida por paciente relacionado")

    def test_odontologo_sin_relacion_no_descarga_adjunto_clinico(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Consulta con adjunto privado",
        )
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile("privado.pdf", b"contenido", content_type="application/pdf"),
            subido_por=self.usuario_odontologo,
        )
        otro_usuario, _ = crear_odontologo(
            username="dr.sin.adjunto",
            matricula="MN-SIN-ADJ",
        )
        self.client.force_login(otro_usuario)

        response = self.client.get(
            reverse("historias:descargar_adjunto", kwargs={"pk": adjunto.pk})
        )

        self.assertEqual(response.status_code, 404)

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

    def test_descarga_adjunto_requiere_odontologo(self):
        historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Consulta con radiografia",
        )
        adjunto = HistoriaClinicaAdjunto.objects.create(
            historia=historia,
            archivo=SimpleUploadedFile("radiografia.pdf", b"contenido", content_type="application/pdf"),
            subido_por=self.usuario_odontologo,
        )
        url = reverse("historias:descargar_adjunto", kwargs={"pk": adjunto.pk})

        response_odontologo = self.client.get(url)
        self.assertEqual(response_odontologo.status_code, 200)
        self.assertEqual(b"".join(response_odontologo.streaming_content), b"contenido")

        usuario_recepcionista = get_user_model().objects.create_user(
            username="recepcion.adjunto"
        )
        asignar_rol(usuario_recepcionista, ROL_RECEPCIONISTA)
        self.client.force_login(usuario_recepcionista)

        response_recepcionista = self.client.get(url)

        self.assertEqual(response_recepcionista.status_code, 403)

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


class HistoriaClinicaStorageBackupTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.backup_dir = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_dir.name,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                },
            },
        )
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_dir.cleanup)
        self.addCleanup(self.backup_dir.cleanup)
        self.paciente = Paciente.objects.create(
            nombre="Backup",
            apellido="Storage",
            documento="59999111",
        )
        self.usuario_odontologo, self.odontologo = crear_odontologo(
            username="dr.backup",
            matricula="MN-BACKUP",
        )
        self.historia = HistoriaClinica.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            creado_por=self.usuario_odontologo,
            actualizado_por=self.usuario_odontologo,
            fecha=timezone.localdate(),
            motivo_consulta="Backup de adjuntos",
        )

    def test_backup_storage_historias_descarga_adjuntos_y_manifest(self):
        HistoriaClinicaAdjunto.objects.create(
            historia=self.historia,
            archivo=SimpleUploadedFile(
                "radiografia.pdf",
                b"contenido-clinico",
                content_type="application/pdf",
            ),
            subido_por=self.usuario_odontologo,
        )

        salida = StringIO()
        call_command(
            "backup_storage_historias",
            output_dir=self.backup_dir.name,
            stdout=salida,
        )

        backup_generado = next(Path(self.backup_dir.name).glob("historias-storage-*"))
        manifest_path = backup_generado / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        archivo_backup = backup_generado / manifest["archivos"][0]["ruta_backup"]

        self.assertTrue(manifest_path.exists())
        self.assertEqual(manifest["total_adjuntos"], 1)
        self.assertEqual(manifest["total_bytes"], len(b"contenido-clinico"))
        self.assertEqual(archivo_backup.read_bytes(), b"contenido-clinico")
        self.assertEqual(len(manifest["archivos"][0]["sha256"]), 64)
        self.assertIn("Backup de Storage creado", salida.getvalue())

    def test_backup_storage_historias_dry_run_no_descarga_archivos(self):
        HistoriaClinicaAdjunto.objects.create(
            historia=self.historia,
            archivo=SimpleUploadedFile(
                "radiografia.pdf",
                b"contenido-clinico",
                content_type="application/pdf",
            ),
            subido_por=self.usuario_odontologo,
        )

        salida = StringIO()
        call_command(
            "backup_storage_historias",
            output_dir=self.backup_dir.name,
            dry_run=True,
            stdout=salida,
        )

        self.assertFalse(list(Path(self.backup_dir.name).glob("historias-storage-*")))
        self.assertIn("Adjuntos a respaldar: 1", salida.getvalue())
