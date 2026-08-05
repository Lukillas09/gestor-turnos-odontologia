from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from pacientes.models import Paciente
from turnos.models import (
    AccionPublicaTurno,
    DisponibilidadOdontologo,
    Odontologo,
    SolicitudTurnoPublica,
    Turno,
    bloquear_agendas_de_turnos,
)
from turnos.notifications import ResultadoNotificacionEmail
from turnos.public_access.services import (
    cancelar_turno_publico_seguro,
    reprogramar_turno_publico_seguro,
)
from turnos.services import confirmar_turno_con_duracion
from turnos.solicitudes_publicas.services import crear_solicitud_publica_de_turno


@skipUnless(
    connection.vendor == "postgresql",
    "La concurrencia de agenda requiere PostgreSQL real; SQLite no implementa row locks.",
)
@override_settings(
    TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=False,
    TURNOS_PUBLIC_BOOKING_MAX_PENDING_PER_DNI=10,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AgendaConcurrentePostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.fecha = timezone.localdate() + timedelta(days=7)
        self.odontologo_a = self._crear_odontologo("a")
        self.odontologo_b = self._crear_odontologo("b")

    def test_dos_pacientes_mismo_horario_tienen_un_solo_ganador(self):
        barrera = Barrier(2)

        def reservar(indice):
            odontologo = Odontologo.objects.get(pk=self.odontologo_a.pk)
            barrera.wait(timeout=10)
            try:
                crear_solicitud_publica_de_turno(
                    self._datos_publicos(indice, odontologo, time(10, 0))
                )
                return "creada"
            except ValidationError:
                return "conflicto"

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            resultados = list(
                executor.map(lambda indice: self._en_worker(reservar, indice), (1, 2))
            )

        self.assertEqual(resultados.count("creada"), 1)
        self.assertEqual(resultados.count("conflicto"), 1)
        self.assertEqual(self._turnos_en(self.odontologo_a, time(10, 0)).count(), 1)
        self._assert_sin_solapamientos(self.odontologo_a)

    def test_paciente_y_recepcion_mismo_horario_tienen_un_solo_ganador(self):
        barrera = Barrier(2)
        paciente_interno = self._crear_paciente("interno", "70000001")

        def reservar_publico():
            odontologo = Odontologo.objects.get(pk=self.odontologo_a.pk)
            barrera.wait(timeout=10)
            try:
                crear_solicitud_publica_de_turno(self._datos_publicos(3, odontologo, time(10, 0)))
                return "publico"
            except ValidationError:
                return "conflicto"

        def reservar_interno():
            odontologo = Odontologo.objects.get(pk=self.odontologo_a.pk)
            paciente = Paciente.objects.get(pk=paciente_interno.pk)
            barrera.wait(timeout=10)
            try:
                Turno.objects.create(
                    paciente=paciente,
                    odontologo=odontologo,
                    fecha=self.fecha,
                    hora_inicio=time(10, 0),
                    duracion_minutos=30,
                    estado=Turno.Estado.CONFIRMADO,
                )
                return "interno"
            except ValidationError:
                return "conflicto"

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futuros = [
                executor.submit(self._en_worker, reservar_publico),
                executor.submit(self._en_worker, reservar_interno),
            ]
            resultados = [futuro.result(timeout=20) for futuro in futuros]

        self.assertEqual(resultados.count("conflicto"), 1)
        self.assertEqual(self._turnos_en(self.odontologo_a, time(10, 0)).count(), 1)
        self._assert_sin_solapamientos(self.odontologo_a)

    def test_reprogramacion_compite_con_reserva_nueva_sin_superponer(self):
        turno = self._crear_turno(self.odontologo_a, time(9, 0), "reprogramar")
        accion, token = self._crear_accion(turno, AccionPublicaTurno.TipoAccion.REPROGRAMAR)
        barrera = Barrier(2)

        def reprogramar():
            barrera.wait(timeout=10)
            try:
                ok, _turno = reprogramar_turno_publico_seguro(
                    accion.pk,
                    token,
                    turno.paciente_id,
                    {"fecha": self.fecha, "hora_inicio": time(10, 0)},
                )
                return ok
            except ValidationError:
                return False

        def reservar():
            odontologo = Odontologo.objects.get(pk=self.odontologo_a.pk)
            barrera.wait(timeout=10)
            try:
                crear_solicitud_publica_de_turno(self._datos_publicos(4, odontologo, time(10, 0)))
                return True
            except ValidationError:
                return False

        with (
            patch("turnos.services.programar_integraciones_turno"),
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futuros = [
                executor.submit(self._en_worker, reprogramar),
                executor.submit(self._en_worker, reservar),
            ]
            resultados = [futuro.result(timeout=20) for futuro in futuros]

        self.assertEqual(sum(resultados), 1)
        self.assertEqual(self._turnos_en(self.odontologo_a, time(10, 0)).count(), 1)
        self._assert_sin_solapamientos(self.odontologo_a)

    def test_dos_reprogramaciones_al_mismo_horario_tienen_un_ganador(self):
        turno_a = self._crear_turno(self.odontologo_a, time(9, 0), "reprogramar-a")
        turno_b = self._crear_turno(self.odontologo_a, time(10, 0), "reprogramar-b")
        accion_a, token_a = self._crear_accion(
            turno_a,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        )
        accion_b, token_b = self._crear_accion(
            turno_b,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        )
        barrera = Barrier(2)

        def reprogramar(accion_id, token, paciente_id):
            barrera.wait(timeout=10)
            try:
                ok, _turno = reprogramar_turno_publico_seguro(
                    accion_id,
                    token,
                    paciente_id,
                    {"fecha": self.fecha, "hora_inicio": time(12, 0)},
                )
                return ok
            except ValidationError:
                return False

        with (
            patch("turnos.services.programar_integraciones_turno"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futuros = [
                executor.submit(
                    self._en_worker,
                    reprogramar,
                    accion_a.pk,
                    token_a,
                    turno_a.paciente_id,
                ),
                executor.submit(
                    self._en_worker,
                    reprogramar,
                    accion_b.pk,
                    token_b,
                    turno_b.paciente_id,
                ),
            ]
            resultados = [futuro.result(timeout=20) for futuro in futuros]

        self.assertEqual(sum(resultados), 1)
        self.assertEqual(self._turnos_en(self.odontologo_a, time(12, 0)).count(), 1)
        self._assert_sin_solapamientos(self.odontologo_a)

    def test_cancelacion_contra_confirmacion_deja_un_unico_resultado_valido(self):
        turno = self._crear_turno(self.odontologo_a, time(9, 0), "estado")
        accion, token = self._crear_accion(turno, AccionPublicaTurno.TipoAccion.CANCELAR)
        barrera = Barrier(2)

        def cancelar():
            barrera.wait(timeout=10)
            return cancelar_turno_publico_seguro(
                accion.pk,
                token,
                turno.paciente_id,
                "Cancelación concurrente",
            )

        def confirmar():
            turno_actual = Turno.objects.get(pk=turno.pk)
            barrera.wait(timeout=10)
            return confirmar_turno_con_duracion(turno_actual, 30).confirmado

        with (
            patch("turnos.services.programar_integraciones_turno"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futuros = [
                executor.submit(self._en_worker, cancelar),
                executor.submit(self._en_worker, confirmar),
            ]
            resultados = [futuro.result(timeout=20) for futuro in futuros]

        turno.refresh_from_db()
        accion.refresh_from_db()
        self.assertEqual(sum(resultados), 1)
        self.assertIn(turno.estado, {Turno.Estado.CANCELADO, Turno.Estado.CONFIRMADO})
        self.assertEqual(bool(accion.utilizado_en), turno.estado == Turno.Estado.CANCELADO)

    def test_doble_click_crea_una_solicitud_y_reutiliza_la_misma(self):
        barrera = Barrier(2)

        def reservar(_indice):
            odontologo = Odontologo.objects.get(pk=self.odontologo_a.pk)
            barrera.wait(timeout=10)
            resultado = crear_solicitud_publica_de_turno(
                self._datos_publicos(
                    5,
                    odontologo,
                    time(10, 0),
                    documento="79999995",
                )
            )
            return resultado.turno.pk, resultado.duplicada

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            resultados = list(
                executor.map(lambda indice: self._en_worker(reservar, indice), (1, 2))
            )

        self.assertEqual({resultado[0] for resultado in resultados}, {resultados[0][0]})
        self.assertEqual(sorted(resultado[1] for resultado in resultados), [False, True])
        self.assertEqual(SolicitudTurnoPublica.objects.count(), 1)
        self.assertEqual(self._turnos_en(self.odontologo_a, time(10, 0)).count(), 1)

    def test_horarios_distintos_del_mismo_profesional_progresan_sin_conflicto(self):
        resultados = self._reservas_independientes(
            (self.odontologo_a.pk, time(9, 0), 6),
            (self.odontologo_a.pk, time(11, 0), 7),
        )

        self.assertEqual(resultados, [True, True])
        self._assert_sin_solapamientos(self.odontologo_a)

    def test_profesionales_distintos_progresan_sin_conflicto(self):
        resultados = self._reservas_independientes(
            (self.odontologo_a.pk, time(10, 0), 8),
            (self.odontologo_b.pk, time(10, 0), 9),
        )

        self.assertEqual(resultados, [True, True])
        self.assertEqual(self._turnos_en(self.odontologo_a, time(10, 0)).count(), 1)
        self.assertEqual(self._turnos_en(self.odontologo_b, time(10, 0)).count(), 1)

    def test_google_lento_no_mantiene_lock_de_agenda(self):
        self._assert_proveedor_lento_no_mantiene_lock("google")

    def test_email_lento_no_mantiene_lock_de_agenda(self):
        self._assert_proveedor_lento_no_mantiene_lock("email")

    def _reservas_independientes(self, *reservas):
        barrera = Barrier(len(reservas))

        def reservar(odontologo_id, hora_inicio, indice):
            odontologo = Odontologo.objects.get(pk=odontologo_id)
            barrera.wait(timeout=10)
            crear_solicitud_publica_de_turno(self._datos_publicos(indice, odontologo, hora_inicio))
            return True

        with (
            patch("turnos.solicitudes_publicas.services._notificar_solicitud"),
            ThreadPoolExecutor(max_workers=len(reservas)) as executor,
        ):
            futuros = [executor.submit(self._en_worker, reservar, *reserva) for reserva in reservas]
            return [futuro.result(timeout=20) for futuro in futuros]

    def _assert_proveedor_lento_no_mantiene_lock(self, proveedor):
        turno = self._crear_turno(self.odontologo_a, time(9, 0), f"proveedor-{proveedor}")
        proveedor_iniciado = Event()
        liberar_proveedor = Event()
        lock_adquirido = Event()
        estado_transaccion_proveedor = []

        def proveedor_lento(_turno):
            estado_transaccion_proveedor.append(connection.in_atomic_block)
            proveedor_iniciado.set()
            if not liberar_proveedor.wait(timeout=10):
                raise TimeoutError("La prueba no liberó el proveedor simulado.")
            return ResultadoNotificacionEmail(enviada=True)

        def confirmar():
            turno_actual = Turno.objects.get(pk=turno.pk)
            return confirmar_turno_con_duracion(turno_actual, 30).confirmado

        def tomar_lock():
            with transaction.atomic():
                bloquear_agendas_de_turnos([(self.odontologo_a.pk, self.fecha)])
                lock_adquirido.set()
            return True

        google_side_effect = proveedor_lento if proveedor == "google" else None
        email_side_effect = proveedor_lento if proveedor == "email" else None
        google_return_value = None if proveedor == "google" else object()
        email_return_value = (
            None if proveedor == "email" else ResultadoNotificacionEmail(enviada=True)
        )

        with (
            patch(
                "turnos.google_calendar_sync.sincronizar_turno_actualizado",
                side_effect=google_side_effect,
                return_value=google_return_value,
            ),
            patch(
                "turnos.notifications.notificar_turno_confirmado",
                side_effect=email_side_effect,
                return_value=email_return_value,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futuro_confirmacion = executor.submit(self._en_worker, confirmar)
            self.assertTrue(proveedor_iniciado.wait(timeout=10))
            futuro_lock = executor.submit(self._en_worker, tomar_lock)
            lock_libre = lock_adquirido.wait(timeout=5)
            liberar_proveedor.set()
            self.assertTrue(futuro_confirmacion.result(timeout=20))
            self.assertTrue(futuro_lock.result(timeout=20))

        self.assertTrue(lock_libre)
        self.assertEqual(estado_transaccion_proveedor, [False])

    def _crear_odontologo(self, sufijo):
        usuario = get_user_model().objects.create_user(username=f"concurrencia.{sufijo}")
        odontologo = Odontologo.objects.create(
            usuario=usuario,
            matricula=f"CONC-{sufijo.upper()}",
        )
        DisponibilidadOdontologo.objects.create(
            odontologo=odontologo,
            dia_semana=self.fecha.weekday(),
            hora_inicio=time(8, 0),
            hora_fin=time(18, 0),
        )
        return odontologo

    def _crear_paciente(self, sufijo, documento):
        return Paciente.objects.create(
            nombre="Paciente",
            apellido=f"Concurrente {sufijo}",
            documento=documento,
            telefono="1100000000",
            email=f"{sufijo}@example.test",
        )

    def _crear_turno(self, odontologo, hora_inicio, sufijo):
        paciente = self._crear_paciente(sufijo, f"71{Paciente.objects.count():06d}")
        return Turno.objects.create(
            paciente=paciente,
            odontologo=odontologo,
            fecha=self.fecha,
            hora_inicio=hora_inicio,
            duracion_minutos=30,
            estado=Turno.Estado.PENDIENTE,
        )

    def _crear_accion(self, turno, tipo_accion):
        token = f"token-concurrente-{turno.pk}-{tipo_accion}"
        accion = AccionPublicaTurno.objects.create(
            turno=turno,
            paciente=turno.paciente,
            tipo_accion=tipo_accion,
            token_hash=make_password(token),
            version_turno=turno.version_publica,
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        return accion, token

    def _datos_publicos(self, indice, odontologo, hora_inicio, documento=None):
        return {
            "nombre": "Paciente",
            "apellido": f"Concurrente {indice}",
            "telefono": "1100000000",
            "documento": documento or f"799999{indice:02d}",
            "email": f"concurrente.{indice}@example.test",
            "odontologo": odontologo,
            "fecha": self.fecha,
            "hora_inicio": hora_inicio,
            "motivo": "",
        }

    def _turnos_en(self, odontologo, hora_inicio):
        return Turno.objects.filter(
            odontologo=odontologo,
            fecha=self.fecha,
            hora_inicio=hora_inicio,
            estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        )

    def _assert_sin_solapamientos(self, odontologo):
        turnos = list(
            Turno.objects.filter(
                odontologo=odontologo,
                fecha=self.fecha,
                estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
            ).order_by("hora_inicio", "pk")
        )
        for anterior, siguiente in zip(turnos, turnos[1:], strict=False):
            self.assertLessEqual(anterior.fecha_hora_fin, siguiente.fecha_hora_inicio)

    @staticmethod
    def _en_worker(funcion, *args):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '5s'")
                cursor.execute("SET statement_timeout TO '15s'")
            return funcion(*args)
        finally:
            close_old_connections()
