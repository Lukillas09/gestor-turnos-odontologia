from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from io import StringIO
from secrets import token_urlsafe
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import (
    AccionPublicaTurno,
    DesafioAccesoPublicoTurnos,
    DisponibilidadOdontologo,
    IdempotenciaSolicitudPublica,
    LimitePublico,
    Odontologo,
    SolicitudTurnoPublica,
    Turno,
)
from turnos.public_access.exceptions import (
    MENSAJE_PROTECCION_PUBLICA_NO_DISPONIBLE,
    RETRY_AFTER_PROTECCION_PUBLICA_SECONDS,
    ProteccionPublicaNoDisponible,
)
from turnos.public_access.rate_limit import (
    calcular_ventana_fija,
    incrementar_limite,
    leer_contador,
)
from turnos.public_access.tokens import (
    PUBLIC_ACCESS_PENDING_CHALLENGE_KEY,
    PUBLIC_ACCESS_SESSION_KEY,
    PUBLIC_ACTION_TOKENS_SESSION_KEY,
    hash_valor_publico,
)
from turnos.solicitudes_publicas.proteccion import (
    SESSION_IDEMPOTENCY_KEY,
    IdempotenciaSolicitudPublicaInvalida,
    ProteccionSolicitudPublicaNoDisponible,
    adquirir_idempotencia,
    completar_idempotencia,
    generar_idempotency_token,
    liberar_idempotencia,
)


class SesionPrueba(dict):
    modified = False


def crear_request_idempotencia(token=None, creado_en=None):
    request = SimpleNamespace(session=SesionPrueba())

    if token is None:
        token = generar_idempotency_token(request)
    else:
        token_hash = hash_valor_publico(token, "booking_idempotency")
        request.session[SESSION_IDEMPOTENCY_KEY] = {
            token_hash: creado_en or int(timezone.now().timestamp())
        }

    return request, token


class LimitePublicoTests(TestCase):
    def setUp(self):
        self.ahora = timezone.now().replace(microsecond=0)
        self.sujeto = hash_valor_publico("203.0.113.20", "ip")

    def test_primer_intento_crea_contador_uno(self):
        resultado = incrementar_limite("solicitud_ip", self.sujeto, 3, 60, self.ahora)

        limite = LimitePublico.objects.get()
        self.assertTrue(resultado.permitido)
        self.assertEqual(resultado.contador, 1)
        self.assertEqual(limite.contador, 1)

    def test_incrementos_siguientes_son_exactos_y_bloquean_al_superar(self):
        resultados = [
            incrementar_limite("solicitud_ip", self.sujeto, 2, 60, self.ahora)
            for _indice in range(3)
        ]

        self.assertEqual([resultado.contador for resultado in resultados], [1, 2, 3])
        self.assertEqual([resultado.permitido for resultado in resultados], [True, True, False])
        self.assertEqual(LimitePublico.objects.get().contador, 3)

    def test_limite_cero_no_crea_filas(self):
        resultado = incrementar_limite("solicitud_ip", self.sujeto, 0, 0, self.ahora)

        self.assertTrue(resultado.permitido)
        self.assertEqual(resultado.contador, 0)
        self.assertFalse(LimitePublico.objects.exists())

    def test_ambitos_y_sujetos_distintos_estan_aislados(self):
        otro_sujeto = hash_valor_publico("203.0.113.21", "ip")
        incrementar_limite("solicitud_ip", self.sujeto, 5, 60, self.ahora)
        incrementar_limite("reenvio_ip", self.sujeto, 5, 60, self.ahora)
        incrementar_limite("solicitud_ip", otro_sujeto, 5, 60, self.ahora)

        self.assertEqual(LimitePublico.objects.count(), 3)
        self.assertEqual(leer_contador("solicitud_ip", self.sujeto, 60, self.ahora), 1)
        self.assertEqual(leer_contador("reenvio_ip", self.sujeto, 60, self.ahora), 1)
        self.assertEqual(leer_contador("solicitud_ip", otro_sujeto, 60, self.ahora), 1)

    def test_cruzar_ventana_crea_contador_nuevo(self):
        _inicio, expira_en = calcular_ventana_fija(self.ahora, 60)
        incrementar_limite("solicitud_ip", self.sujeto, 5, 60, self.ahora)

        resultado = incrementar_limite(
            "solicitud_ip",
            self.sujeto,
            5,
            60,
            expira_en + timedelta(seconds=1),
        )

        self.assertEqual(resultado.contador, 1)
        self.assertEqual(LimitePublico.objects.count(), 2)

    def test_fila_expirada_no_afecta_ventana_actual(self):
        inicio, expira_en = calcular_ventana_fija(self.ahora - timedelta(minutes=2), 60)
        LimitePublico.objects.create(
            ambito="solicitud_ip",
            sujeto_hash=self.sujeto,
            ventana_inicio=inicio,
            contador=99,
            expira_en=expira_en,
        )

        self.assertEqual(leer_contador("solicitud_ip", self.sujeto, 60, self.ahora), 0)
        resultado = incrementar_limite("solicitud_ip", self.sujeto, 5, 60, self.ahora)
        self.assertEqual(resultado.contador, 1)

    def test_no_almacena_ip_original(self):
        incrementar_limite("solicitud_ip", self.sujeto, 3, 60, self.ahora)

        limite = LimitePublico.objects.get()
        self.assertEqual(limite.sujeto_hash, self.sujeto)
        self.assertNotEqual(limite.sujeto_hash, "203.0.113.20")
        self.assertNotIn("203.0.113.20", str(limite))

    def test_error_db_se_convierte_en_excepcion_de_dominio(self):
        with patch(
            "turnos.public_access.rate_limit._incrementar_contador_transaccional",
            side_effect=OperationalError("fallo simulado"),
        ):
            with self.assertRaises(ProteccionPublicaNoDisponible):
                incrementar_limite("solicitud_ip", self.sujeto, 3, 60, self.ahora)

    def test_error_db_en_lectura_se_convierte_en_excepcion_de_dominio(self):
        detalle_sensible = "dni=30111222 ip=203.0.113.20 token=secreto"

        with (
            patch.object(
                LimitePublico.objects,
                "filter",
                side_effect=OperationalError(detalle_sensible),
            ),
            self.assertLogs("turnos.public_access.rate_limit", level="WARNING") as logs,
            self.assertRaises(ProteccionPublicaNoDisponible),
        ):
            leer_contador("solicitud_ip", self.sujeto, 60, self.ahora)

        salida = " ".join(logs.output)
        self.assertIn("operation=rate_limit_read", salida)
        self.assertIn("error_type=OperationalError", salida)
        self.assertNotIn(detalle_sensible, salida)
        self.assertNotIn(self.sujeto, salida)

    def test_error_de_programacion_no_se_convierte_en_503(self):
        with patch(
            "turnos.public_access.rate_limit._incrementar_contador_transaccional",
            side_effect=ValueError("fallo de programación"),
        ):
            with self.assertRaisesRegex(ValueError, "fallo de programación"):
                incrementar_limite("solicitud_ip", self.sujeto, 3, 60, self.ahora)

    def test_no_utiliza_cache_django(self):
        with (
            patch("django.core.cache.cache.add") as cache_add,
            patch("django.core.cache.cache.incr") as cache_incr,
            patch("django.core.cache.cache.get") as cache_get,
        ):
            incrementar_limite("solicitud_ip", self.sujeto, 3, 60, self.ahora)
            leer_contador("solicitud_ip", self.sujeto, 60, self.ahora)

        cache_add.assert_not_called()
        cache_incr.assert_not_called()
        cache_get.assert_not_called()

    def test_valida_ambito_hash_ventana_y_timezone(self):
        with self.assertRaises(ValueError):
            incrementar_limite("", self.sujeto, 1, 60, self.ahora)
        with self.assertRaises(ValueError):
            incrementar_limite("solicitud_ip", "", 1, 60, self.ahora)
        with self.assertRaises(ValueError):
            incrementar_limite("solicitud_ip", self.sujeto, 1, 0, self.ahora)
        with self.assertRaises(ValueError):
            incrementar_limite(
                "solicitud_ip",
                self.sujeto,
                1,
                60,
                self.ahora.replace(tzinfo=None),
            )


@override_settings(
    TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS=3600,
    TURNOS_PUBLIC_BOOKING_PROCESSING_SECONDS=120,
)
class IdempotenciaSolicitudPublicaTests(TestCase):
    def test_primer_token_procesa_y_sesion_guarda_solo_hash(self):
        request, token = crear_request_idempotencia()

        resultado = adquirir_idempotencia(request, token)
        idempotencia = IdempotenciaSolicitudPublica.objects.get()
        tokens_session = request.session[SESSION_IDEMPOTENCY_KEY]

        self.assertTrue(resultado.debe_procesar)
        self.assertEqual(idempotencia.token_hash, resultado.token_hash)
        self.assertNotEqual(idempotencia.token_hash, token)
        self.assertNotIn(token, tokens_session)

    def test_segundo_uso_con_lease_activo_devuelve_processing_existing(self):
        request, token = crear_request_idempotencia()
        adquirir_idempotencia(request, token)

        resultado = adquirir_idempotencia(request, token)

        self.assertEqual(resultado.estado, "processing_existing")
        self.assertFalse(resultado.debe_procesar)
        self.assertTrue(resultado.es_repetido)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 1)

    def test_completed_se_reconoce_y_completar_dos_veces_es_seguro(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)

        completar_idempotencia(resultado.token_hash)
        completar_idempotencia(resultado.token_hash)
        repetido = adquirir_idempotencia(request, token)

        idempotencia = IdempotenciaSolicitudPublica.objects.get()
        self.assertEqual(repetido.estado, IdempotenciaSolicitudPublica.Estado.COMPLETED)
        self.assertIsNone(idempotencia.procesamiento_expira_en)

    def test_liberar_processing_permite_reintento(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)

        liberar_idempotencia(resultado.token_hash)
        reintento = adquirir_idempotencia(request, token)

        self.assertTrue(reintento.debe_procesar)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 1)

    def test_liberar_completed_no_elimina_registro(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        completar_idempotencia(resultado.token_hash)

        liberar_idempotencia(resultado.token_hash)

        self.assertTrue(
            IdempotenciaSolicitudPublica.objects.filter(token_hash=resultado.token_hash).exists()
        )

    def test_lease_vencido_permite_recuperacion(self):
        request, token = crear_request_idempotencia()
        primer_resultado = adquirir_idempotencia(request, token)
        IdempotenciaSolicitudPublica.objects.filter(token_hash=primer_resultado.token_hash).update(
            procesamiento_expira_en=timezone.now() - timedelta(seconds=1)
        )

        recuperado = adquirir_idempotencia(request, token)

        self.assertTrue(recuperado.debe_procesar)

    def test_registro_expirado_se_reclama_si_token_session_sigue_vigente(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        IdempotenciaSolicitudPublica.objects.filter(token_hash=resultado.token_hash).update(
            expira_en=timezone.now() - timedelta(seconds=1)
        )

        recuperado = adquirir_idempotencia(request, token)

        self.assertTrue(recuperado.debe_procesar)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 1)

    def test_token_ausente_o_vencido_es_invalido(self):
        request = SimpleNamespace(session=SesionPrueba())
        with self.assertRaises(IdempotenciaSolicitudPublicaInvalida):
            adquirir_idempotencia(request, "token-ausente")

        token = token_urlsafe(32)
        request, token = crear_request_idempotencia(
            token,
            creado_en=int((timezone.now() - timedelta(hours=2)).timestamp()),
        )
        with self.assertRaises(IdempotenciaSolicitudPublicaInvalida):
            adquirir_idempotencia(request, token)

    def test_dos_tokens_distintos_no_se_bloquean(self):
        request_a, token_a = crear_request_idempotencia()
        request_b, token_b = crear_request_idempotencia()

        resultado_a = adquirir_idempotencia(request_a, token_a)
        resultado_b = adquirir_idempotencia(request_b, token_b)

        self.assertTrue(resultado_a.debe_procesar)
        self.assertTrue(resultado_b.debe_procesar)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 2)

    def test_rollback_no_deja_completed_y_commit_si(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                completar_idempotencia(resultado.token_hash)
                raise RuntimeError("rollback simulado")

        idempotencia = IdempotenciaSolicitudPublica.objects.get()
        self.assertEqual(idempotencia.estado, IdempotenciaSolicitudPublica.Estado.PROCESSING)

        with transaction.atomic():
            completar_idempotencia(resultado.token_hash)

        idempotencia.refresh_from_db()
        self.assertEqual(idempotencia.estado, IdempotenciaSolicitudPublica.Estado.COMPLETED)

    def test_error_db_se_convierte_en_503_de_dominio(self):
        request, token = crear_request_idempotencia()

        with patch(
            "turnos.solicitudes_publicas.proteccion._adquirir_idempotencia_transaccional",
            side_effect=OperationalError("fallo simulado"),
        ):
            with self.assertRaises(ProteccionSolicitudPublicaNoDisponible):
                adquirir_idempotencia(request, token)

    def test_error_db_al_completar_se_convierte_en_503_de_dominio(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)

        with patch.object(
            IdempotenciaSolicitudPublica.objects,
            "select_for_update",
            side_effect=OperationalError("fallo simulado al completar"),
        ):
            with self.assertRaises(ProteccionSolicitudPublicaNoDisponible):
                completar_idempotencia(resultado.token_hash)

        idempotencia = IdempotenciaSolicitudPublica.objects.get()
        self.assertEqual(idempotencia.estado, IdempotenciaSolicitudPublica.Estado.PROCESSING)

    def test_error_db_al_liberar_se_convierte_en_503_de_dominio(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)

        with patch.object(
            IdempotenciaSolicitudPublica.objects,
            "select_for_update",
            side_effect=OperationalError("fallo simulado al liberar"),
        ):
            with self.assertRaises(ProteccionSolicitudPublicaNoDisponible):
                liberar_idempotencia(resultado.token_hash)

        self.assertTrue(
            IdempotenciaSolicitudPublica.objects.filter(
                token_hash=resultado.token_hash,
                estado=IdempotenciaSolicitudPublica.Estado.PROCESSING,
            ).exists()
        )

    def test_error_db_al_recuperar_lease_se_convierte_en_503(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        IdempotenciaSolicitudPublica.objects.filter(token_hash=resultado.token_hash).update(
            procesamiento_expira_en=timezone.now() - timedelta(seconds=1)
        )

        with patch(
            "turnos.solicitudes_publicas.proteccion._reclamar_idempotencia",
            side_effect=OperationalError("fallo simulado al recuperar lease"),
        ):
            with self.assertRaises(ProteccionSolicitudPublicaNoDisponible):
                adquirir_idempotencia(request, token)

    def test_value_error_de_idempotencia_no_se_convierte_en_503(self):
        request, token = crear_request_idempotencia()

        with patch(
            "turnos.solicitudes_publicas.proteccion._adquirir_idempotencia_transaccional",
            side_effect=ValueError("fallo de programación"),
        ):
            with self.assertRaisesRegex(ValueError, "fallo de programación"):
                adquirir_idempotencia(request, token)

    def test_no_utiliza_cache_django(self):
        request, token = crear_request_idempotencia()

        with (
            patch("django.core.cache.cache.add") as cache_add,
            patch("django.core.cache.cache.get") as cache_get,
            patch("django.core.cache.cache.set") as cache_set,
            patch("django.core.cache.cache.delete") as cache_delete,
        ):
            resultado = adquirir_idempotencia(request, token)
            completar_idempotencia(resultado.token_hash)
            liberar_idempotencia(resultado.token_hash)

        cache_add.assert_not_called()
        cache_get.assert_not_called()
        cache_set.assert_not_called()
        cache_delete.assert_not_called()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="turnos@example.test",
    TURNSTILE_ENABLED=False,
    TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=False,
    TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT=10,
    TURNOS_PUBLIC_ACTION_LIMIT=10,
    TURNOS_PUBLIC_RESEND_LIMIT=10,
    TURNOS_PUBLIC_RESEND_SECONDS=0,
)
class ProteccionesPublicasFailClosedTests(TestCase):
    def setUp(self):
        mail.outbox.clear()
        usuario = get_user_model().objects.create_user(username="protecciones.publicas")
        self.odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula="PROT-PUB-001",
        )
        self.fecha = timezone.localdate() + timedelta(days=7)
        DisponibilidadOdontologo.objects.create(
            odontologo=self.odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(8, 0),
            hora_fin=time(18, 0),
        )
        self.paciente = Paciente.objects.create(
            nombre="Paciente",
            apellido="Protegido",
            documento="76543111",
            telefono="1100001111",
            email="paciente@example.test",
            email_verificado_en=timezone.now(),
        )
        self.turno = Turno.objects.create(
            paciente=self.paciente,
            odontologo=self.odontologo,
            fecha=self.fecha,
            hora_inicio=time(9, 0),
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

    def test_solicitud_otp_falla_cerrado_sin_enumerar_ni_enviar(self):
        detalle_sensible = f"dni={self.paciente.documento} email={self.paciente.email}"
        respuestas = []

        for documento in (self.paciente.documento, "99999999"):
            with (
                patch(
                    "turnos.public_access.services._crear_desafio_persistente",
                    side_effect=OperationalError(detalle_sensible),
                ),
                self.assertLogs("turnos.public_access.services", level="WARNING") as logs,
                self.captureOnCommitCallbacks(execute=False) as callbacks,
            ):
                respuesta = self.client.post(
                    reverse("turnos:acceso_publico_solicitar"),
                    {"documento": documento},
                )

            self._assert_respuesta_503(respuesta)
            self.assertEqual(callbacks, [])
            self.assertNotIn(detalle_sensible, " ".join(logs.output))
            respuestas.append(respuesta.content)

        self.assertEqual(respuestas[0], respuestas[1])
        self.assertFalse(DesafioAccesoPublicoTurnos.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_reenvio_otp_falla_cerrado_sin_cambiar_hash_ni_enviar(self):
        desafio = self._crear_desafio_pendiente()
        hash_anterior = desafio.codigo_hash

        with (
            patch.object(
                DesafioAccesoPublicoTurnos.objects,
                "select_for_update",
                side_effect=OperationalError("otp=123456 token=secreto"),
            ),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            respuesta = self.client.post(
                reverse("turnos:acceso_publico_verificar"),
                {"accion": "reenviar"},
            )

        desafio.refresh_from_db()
        self._assert_respuesta_503(respuesta)
        self.assertEqual(desafio.codigo_hash, hash_anterior)
        self.assertEqual(desafio.cantidad_envios, 0)
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_verificacion_otp_falla_cerrado_sin_consumir_desafio(self):
        desafio = self._crear_desafio_pendiente()

        with patch(
            "turnos.public_access.services._validar_codigo_acceso_publico_persistente",
            side_effect=OperationalError("otp=123456"),
        ):
            respuesta = self.client.post(
                reverse("turnos:acceso_publico_verificar"),
                {"codigo": "123456"},
            )

        desafio.refresh_from_db()
        self._assert_respuesta_503(respuesta)
        self.assertIsNone(desafio.validado_en)
        self.assertIsNone(desafio.invalidado_en)
        self.assertEqual(desafio.intentos_fallidos, 0)
        self.assertNotIn(PUBLIC_ACCESS_SESSION_KEY, self.client.session)

    def test_generacion_de_acciones_falla_cerrado_sin_tocar_sesion(self):
        self._habilitar_sesion_publica()

        with patch(
            "turnos.public_access.services._generar_permisos_persistentes",
            side_effect=OperationalError("token_hash=secreto"),
        ):
            respuesta = self.client.get(reverse("turnos:mis_turnos_publico"))

        self._assert_respuesta_503(respuesta)
        self.assertFalse(AccionPublicaTurno.objects.exists())
        self.assertNotIn(PUBLIC_ACTION_TOKENS_SESSION_KEY, self.client.session)

    def test_cancelacion_falla_cerrado_sin_mutar_turno_accion_ni_callback(self):
        accion, token = self._crear_accion(AccionPublicaTurno.TipoAccion.CANCELAR)

        with (
            patch(
                "turnos.public_access.services.bloquear_agendas_de_turnos",
                side_effect=OperationalError("dni=76543111 token=secreto"),
            ),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            respuesta = self.client.post(
                reverse("turnos:mis_turnos_cancelar", kwargs={"accion_id": accion.pk}),
                {
                    "accion_token": token,
                    "motivo_cancelacion": "No puedo asistir.",
                },
            )

        self.turno.refresh_from_db()
        accion.refresh_from_db()
        self._assert_respuesta_503(respuesta)
        self.assertEqual(self.turno.estado, Turno.Estado.PENDIENTE)
        self.assertIsNone(accion.utilizado_en)
        self.assertIsNone(accion.revocado_en)
        self.assertEqual(callbacks, [])

    def test_reprogramacion_falla_cerrado_sin_mutar_turno_accion_ni_callback(self):
        accion, token = self._crear_accion(AccionPublicaTurno.TipoAccion.REPROGRAMAR)

        with (
            patch(
                "turnos.public_access.services.bloquear_agendas_de_turnos",
                side_effect=OperationalError("email=paciente@example.test"),
            ),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            respuesta = self.client.post(
                reverse("turnos:mis_turnos_reprogramar", kwargs={"accion_id": accion.pk}),
                {
                    "accion_token": token,
                    "fecha": self.fecha.isoformat(),
                    "hora_inicio": "10:00",
                },
            )

        self.turno.refresh_from_db()
        accion.refresh_from_db()
        self._assert_respuesta_503(respuesta)
        self.assertEqual(self.turno.hora_inicio, time(9, 0))
        self.assertIsNone(accion.utilizado_en)
        self.assertIsNone(accion.revocado_en)
        self.assertEqual(callbacks, [])

    def test_horarios_reprogramacion_fallan_cerrado_con_respuesta_neutral(self):
        accion, _token = self._crear_accion(AccionPublicaTurno.TipoAccion.REPROGRAMAR)

        with patch(
            "turnos.public_access.views.validar_accion_publica_sin_consumir",
            side_effect=ProteccionPublicaNoDisponible(),
        ):
            respuesta = self.client.get(
                reverse(
                    "turnos:mis_turnos_reprogramar_horarios",
                    kwargs={"accion_id": accion.pk},
                ),
                {"fecha": self.fecha.isoformat()},
            )

        self._assert_respuesta_503(respuesta)

    def test_adquisicion_idempotencia_falla_sin_crear_solicitud_ni_callback(self):
        datos = self._datos_reserva_publica()

        with (
            patch(
                "turnos.solicitudes_publicas.proteccion._adquirir_idempotencia_transaccional",
                side_effect=OperationalError("dni=88777111 token=secreto"),
            ),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            respuesta = self.client.post(
                reverse("turnos:solicitud_publica_datos"),
                datos,
            )

        self._assert_respuesta_booking_503(respuesta)
        self.assertEqual(Turno.objects.count(), 1)
        self.assertFalse(Paciente.objects.filter(documento="88777111").exists())
        self.assertFalse(SolicitudTurnoPublica.objects.exists())
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_fallo_al_completar_idempotencia_revierte_reserva_y_callback(self):
        datos = self._datos_reserva_publica()

        with (
            patch(
                "turnos.views.public_booking.completar_idempotencia",
                side_effect=ProteccionSolicitudPublicaNoDisponible(
                    retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
                ),
            ),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            respuesta = self.client.post(
                reverse("turnos:solicitud_publica_datos"),
                datos,
            )

        self._assert_respuesta_booking_503(respuesta)
        self.assertEqual(Turno.objects.count(), 1)
        self.assertFalse(Paciente.objects.filter(documento="88777111").exists())
        self.assertFalse(SolicitudTurnoPublica.objects.exists())
        self.assertEqual(callbacks, [])
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            IdempotenciaSolicitudPublica.objects.get().estado,
            IdempotenciaSolicitudPublica.Estado.PROCESSING,
        )

    def _crear_desafio_pendiente(self):
        desafio = DesafioAccesoPublicoTurnos.objects.create(
            paciente=self.paciente,
            canal=DesafioAccesoPublicoTurnos.Canal.EMAIL,
            codigo_hash=make_password("123456"),
            expira_en=timezone.now() + timedelta(minutes=10),
            ip_hash="a" * 64,
            dni_hash="b" * 64,
        )
        session = self.client.session
        session[PUBLIC_ACCESS_PENDING_CHALLENGE_KEY] = str(desafio.pk)
        session.save()
        return desafio

    def _habilitar_sesion_publica(self):
        session = self.client.session
        session[PUBLIC_ACCESS_SESSION_KEY] = {"paciente_id": self.paciente.pk}
        session.save()

    def _crear_accion(self, tipo_accion):
        self._habilitar_sesion_publica()
        token = token_urlsafe(32)
        accion = AccionPublicaTurno.objects.create(
            turno=self.turno,
            paciente=self.paciente,
            tipo_accion=tipo_accion,
            token_hash=make_password(token),
            version_turno=self.turno.version_publica,
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        session = self.client.session
        session[PUBLIC_ACTION_TOKENS_SESSION_KEY] = {str(accion.pk): token}
        session.save()
        return accion, token

    def _datos_reserva_publica(self):
        request, token = crear_request_idempotencia()
        session = self.client.session
        session[SESSION_IDEMPOTENCY_KEY] = request.session[SESSION_IDEMPOTENCY_KEY]
        session.save()
        return {
            "nombre": "Paciente",
            "apellido": "Reserva",
            "documento": "88777111",
            "telefono": "1100002222",
            "email": "reserva@example.test",
            "odontologo": self.odontologo.pk,
            "fecha": self.fecha.isoformat(),
            "hora_inicio": "11:00",
            "motivo": "Consulta de prueba",
            "idempotency_token": token,
        }

    def _assert_respuesta_503(self, respuesta):
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(
            respuesta.headers["Retry-After"],
            str(RETRY_AFTER_PROTECCION_PUBLICA_SECONDS),
        )
        self.assertContains(
            respuesta,
            MENSAJE_PROTECCION_PUBLICA_NO_DISPONIBLE,
            status_code=503,
        )
        self.assertNotContains(
            respuesta,
            self.paciente.documento,
            status_code=503,
        )
        self.assertNotContains(
            respuesta,
            self.paciente.email,
            status_code=503,
        )

    def _assert_respuesta_booking_503(self, respuesta):
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(
            respuesta.headers["Retry-After"],
            str(RETRY_AFTER_PROTECCION_PUBLICA_SECONDS),
        )
        self.assertContains(
            respuesta,
            "No pudimos registrar la solicitud en este momento",
            status_code=503,
        )


class LimpiarProteccionesPublicasCommandTests(TestCase):
    def setUp(self):
        ahora = timezone.now()
        self.limite_expirado = LimitePublico.objects.create(
            ambito="solicitud_ip",
            sujeto_hash="a" * 40,
            ventana_inicio=ahora - timedelta(minutes=2),
            contador=2,
            expira_en=ahora - timedelta(minutes=1),
        )
        self.limite_activo = LimitePublico.objects.create(
            ambito="solicitud_ip",
            sujeto_hash="b" * 40,
            ventana_inicio=ahora,
            contador=1,
            expira_en=ahora + timedelta(minutes=1),
        )
        self.idempotencia_expirada = IdempotenciaSolicitudPublica.objects.create(
            token_hash="c" * 40,
            expira_en=ahora - timedelta(minutes=1),
        )
        self.idempotencia_activa = IdempotenciaSolicitudPublica.objects.create(
            token_hash="d" * 40,
            expira_en=ahora + timedelta(minutes=1),
        )

    def test_dry_run_informa_sin_eliminar(self):
        salida = StringIO()

        call_command("limpiar_protecciones_publicas", "--dry-run", stdout=salida)

        self.assertIn("Simulación: límites=1; idempotencias=1", salida.getvalue())
        self.assertEqual(LimitePublico.objects.count(), 2)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 2)

    def test_elimina_expirados_en_lotes_y_conserva_activos(self):
        salida = StringIO()

        call_command(
            "limpiar_protecciones_publicas",
            "--batch-size=1",
            "--max-batches=2",
            stdout=salida,
        )

        self.assertIn("Resultado: límites=1; idempotencias=1", salida.getvalue())
        self.assertFalse(LimitePublico.objects.filter(pk=self.limite_expirado.pk).exists())
        self.assertTrue(LimitePublico.objects.filter(pk=self.limite_activo.pk).exists())
        self.assertFalse(
            IdempotenciaSolicitudPublica.objects.filter(pk=self.idempotencia_expirada.pk).exists()
        )
        self.assertTrue(
            IdempotenciaSolicitudPublica.objects.filter(pk=self.idempotencia_activa.pk).exists()
        )

    def test_rechaza_limites_de_lote_invalidos(self):
        with self.assertRaises(CommandError):
            call_command("limpiar_protecciones_publicas", "--batch-size=0")
        with self.assertRaises(CommandError):
            call_command("limpiar_protecciones_publicas", "--max-batches=0")


@skipUnless(
    connection.vendor == "postgresql",
    "La concurrencia de protecciones públicas requiere PostgreSQL real.",
)
@override_settings(
    TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS=3600,
    TURNOS_PUBLIC_BOOKING_PROCESSING_SECONDS=120,
)
class ProteccionesPublicasPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def test_incrementos_concurrentes_mismo_sujeto_son_exactos(self):
        cantidad = 6
        barrera = Barrier(cantidad)
        ahora = timezone.now().replace(microsecond=0)
        sujeto = hash_valor_publico("203.0.113.30", "ip")

        def incrementar(_indice):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    return incrementar_limite("solicitud_ip", sujeto, 3, 60, ahora)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=cantidad) as executor:
            resultados = list(executor.map(incrementar, range(cantidad)))

        limite = LimitePublico.objects.get()
        self.assertEqual(limite.contador, cantidad)
        self.assertEqual(sorted(resultado.contador for resultado in resultados), list(range(1, 7)))
        self.assertEqual(sum(resultado.permitido for resultado in resultados), 3)

    def test_incrementos_concurrentes_claves_distintas_no_se_mezclan(self):
        sujetos = [
            hash_valor_publico("203.0.113.31", "ip"),
            hash_valor_publico("203.0.113.32", "ip"),
        ]
        barrera = Barrier(4)
        ahora = timezone.now().replace(microsecond=0)

        def incrementar(indice):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return incrementar_limite(
                    "solicitud_ip" if indice < 2 else "reenvio_ip",
                    sujetos[indice % 2],
                    5,
                    60,
                    ahora,
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(incrementar, range(4)))

        self.assertEqual(LimitePublico.objects.count(), 4)
        self.assertTrue(
            all(valor == 1 for valor in LimitePublico.objects.values_list("contador", flat=True))
        )

    def test_ventanas_distintas_del_mismo_sujeto_no_se_mezclan(self):
        sujeto = hash_valor_publico("203.0.113.33", "ip")
        ahora = timezone.now().replace(microsecond=0)
        momentos = [ahora, ahora + timedelta(seconds=120)]
        barrera = Barrier(2)

        def incrementar(momento):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return incrementar_limite("solicitud_ip", sujeto, 5, 60, momento)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(incrementar, momentos))

        self.assertEqual([resultado.contador for resultado in resultados], [1, 1])
        self.assertEqual(LimitePublico.objects.count(), 2)

    def test_un_solo_worker_adquiere_mismo_token(self):
        token = token_urlsafe(32)
        token_hash = hash_valor_publico(token, "booking_idempotency")
        creado_en = int(timezone.now().timestamp())
        barrera = Barrier(2)

        def adquirir(_indice):
            close_old_connections()
            request, _token = crear_request_idempotencia(token, creado_en)
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    return adquirir_idempotencia(request, token).estado
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            estados = list(executor.map(adquirir, range(2)))

        self.assertEqual(estados.count("processing"), 1)
        self.assertEqual(estados.count("processing_existing"), 1)
        self.assertEqual(
            IdempotenciaSolicitudPublica.objects.filter(token_hash=token_hash).count(),
            1,
        )

    def test_tokens_distintos_se_adquieren_en_paralelo(self):
        tokens = [token_urlsafe(32), token_urlsafe(32)]
        barrera = Barrier(2)

        def adquirir(token):
            close_old_connections()
            request, _token = crear_request_idempotencia(token)
            try:
                barrera.wait(timeout=10)
                return adquirir_idempotencia(request, token).estado
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            estados = list(executor.map(adquirir, tokens))

        self.assertEqual(estados, ["processing", "processing"])
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 2)

    def test_lease_vencido_se_recupera_en_postgresql(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        IdempotenciaSolicitudPublica.objects.filter(token_hash=resultado.token_hash).update(
            procesamiento_expira_en=timezone.now() - timedelta(seconds=1)
        )

        recuperado = adquirir_idempotencia(request, token)

        self.assertTrue(recuperado.debe_procesar)
        self.assertEqual(IdempotenciaSolicitudPublica.objects.count(), 1)

    def test_completar_mientras_llega_retry_no_crea_segundo_procesador(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        barrera = Barrier(2)

        def completar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    completar_idempotencia(resultado.token_hash)
                return "completed"
            finally:
                close_old_connections()

        def reintentar():
            close_old_connections()
            request_retry, _token = crear_request_idempotencia(token)
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    return adquirir_idempotencia(request_retry, token).estado
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuro_completar = executor.submit(completar)
            futuro_retry = executor.submit(reintentar)
            self.assertEqual(futuro_completar.result(), "completed")
            estado_retry = futuro_retry.result()

        self.assertIn(estado_retry, {"processing_existing", "completed"})
        idempotencia = IdempotenciaSolicitudPublica.objects.get()
        self.assertEqual(idempotencia.estado, IdempotenciaSolicitudPublica.Estado.COMPLETED)

    def test_liberar_mientras_llega_retry_es_seguro(self):
        request, token = crear_request_idempotencia()
        resultado = adquirir_idempotencia(request, token)
        barrera = Barrier(2)

        def liberar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    liberar_idempotencia(resultado.token_hash)
                return "released"
            finally:
                close_old_connections()

        def reintentar():
            close_old_connections()
            request_retry, _token = crear_request_idempotencia(token)
            try:
                barrera.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    return adquirir_idempotencia(request_retry, token).estado
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuro_liberar = executor.submit(liberar)
            futuro_retry = executor.submit(reintentar)
            self.assertEqual(futuro_liberar.result(), "released")
            estado_retry = futuro_retry.result()

        self.assertIn(estado_retry, {"processing", "processing_existing"})
        self.assertLessEqual(IdempotenciaSolicitudPublica.objects.count(), 1)
