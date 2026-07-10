import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.text import get_valid_filename

from .fields import EncryptedTextField

logger = logging.getLogger(__name__)

MAX_FOTO_ODONTOLOGO_BYTES = 5 * 1024 * 1024
EXTENSIONES_FOTO_ODONTOLOGO_PERMITIDAS = {".jpeg", ".jpg", ".png", ".webp"}
CONTENT_TYPES_FOTO_ODONTOLOGO_PERMITIDOS = {"image/jpeg", "image/png", "image/webp"}


def ruta_foto_odontologo(instance, filename):
    nombre_archivo = get_valid_filename(Path(filename).name)
    odontologo_id = instance.pk or "sin-id"
    return f"odontologos/{odontologo_id}/perfil/{uuid4().hex}_{nombre_archivo}"


def validar_foto_odontologo(archivo):
    if not archivo:
        return

    extension = Path(archivo.name).suffix.lower()

    if extension not in EXTENSIONES_FOTO_ODONTOLOGO_PERMITIDAS:
        raise ValidationError("La foto debe ser JPG, PNG o WEBP.")

    content_type = getattr(archivo, "content_type", "")
    if content_type and content_type not in CONTENT_TYPES_FOTO_ODONTOLOGO_PERMITIDOS:
        raise ValidationError("El archivo seleccionado debe ser una imagen.")

    if archivo.size > MAX_FOTO_ODONTOLOGO_BYTES:
        raise ValidationError("La foto no puede superar los 5 MB.")


class Odontologo(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_odontologo",
    )
    matricula = models.CharField(max_length=50, unique=True)
    celular = models.CharField(max_length=30, blank=True)
    especialidad = models.CharField(max_length=100, blank=True)
    duracion_turno_minutos = models.PositiveSmallIntegerField(default=30)
    hora_inicio_atencion = models.TimeField(default=time(9, 0))
    hora_fin_atencion = models.TimeField(default=time(18, 0))
    color_calendario = models.CharField(max_length=7, default="#2f80ed")
    foto_url = models.URLField(blank=True)
    foto_perfil = models.FileField(
        upload_to=ruta_foto_odontologo,
        validators=[validar_foto_odontologo],
        blank=True,
    )
    foto_posicion_x = models.PositiveSmallIntegerField(default=50)
    foto_posicion_y = models.PositiveSmallIntegerField(default=50)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["usuario__last_name", "usuario__first_name", "usuario__username"]
        verbose_name = "Odontólogo"
        verbose_name_plural = "Odontólogos"

    def clean(self):
        errors = {}

        if self.duracion_turno_minutos <= 0:
            errors["duracion_turno_minutos"] = "La duración debe ser mayor a 0."

        if self.hora_inicio_atencion >= self.hora_fin_atencion:
            errors["hora_fin_atencion"] = "La hora de fin debe ser posterior al inicio."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def nombre_completo(self):
        full_name = self.usuario.get_full_name()
        return full_name or self.usuario.username

    @property
    def foto_perfil_url(self):
        if self.foto_perfil:
            try:
                return self.foto_perfil.url
            except Exception:
                logger.warning(
                    "No se pudo obtener la URL de la foto del odontologo %s.",
                    self.pk,
                    exc_info=True,
                )

        return self.foto_url

    @property
    def foto_object_position(self):
        return f"{self.foto_posicion_x}% {self.foto_posicion_y}%"

    def __str__(self):
        return self.nombre_completo


class DisponibilidadOdontologo(models.Model):
    class DiaSemana(models.IntegerChoices):
        LUNES = 0, "Lunes"
        MARTES = 1, "Martes"
        MIERCOLES = 2, "Miércoles"
        JUEVES = 3, "Jueves"
        VIERNES = 4, "Viernes"
        SABADO = 5, "Sábado"
        DOMINGO = 6, "Domingo"

    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.CASCADE,
        related_name="disponibilidades",
    )
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["odontologo", "dia_semana", "hora_inicio"]
        verbose_name = "Disponibilidad de odontólogo"
        verbose_name_plural = "Disponibilidades de odontólogos"
        indexes = [
            models.Index(fields=["odontologo", "dia_semana", "activo"]),
        ]

    def clean(self):
        errors = {}

        if self.hora_inicio >= self.hora_fin:
            errors["hora_fin"] = "La hora de fin debe ser posterior al inicio."

        if not errors and self.activo:
            disponibilidades = DisponibilidadOdontologo.objects.filter(
                odontologo=self.odontologo,
                dia_semana=self.dia_semana,
                activo=True,
            )

            if self.pk:
                disponibilidades = disponibilidades.exclude(pk=self.pk)

            for disponibilidad in disponibilidades:
                if (
                    self.hora_inicio < disponibilidad.hora_fin
                    and self.hora_fin > disponibilidad.hora_inicio
                ):
                    errors["hora_inicio"] = "Ya existe una disponibilidad superpuesta para ese día."
                    break

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.odontologo} - {self.get_dia_semana_display()} "
            f"{self.hora_inicio:%H:%M} a {self.hora_fin:%H:%M}"
        )


class BloqueoAgendaOdontologo(models.Model):
    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.CASCADE,
        related_name="bloqueos_agenda",
    )
    fecha = models.DateField()
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["odontologo", "fecha"]
        verbose_name = "Bloqueo de agenda"
        verbose_name_plural = "Bloqueos de agenda"
        indexes = [
            models.Index(fields=["odontologo", "fecha"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["odontologo", "fecha"],
                name="uniq_bloqueo_agenda_odontologo_fecha",
            )
        ]

    def __str__(self):
        return f"{self.odontologo} - {self.fecha:%d/%m/%Y}"


def bloquear_agendas_de_turnos(claves_agenda):
    claves = sorted(
        {
            (odontologo_id, fecha)
            for odontologo_id, fecha in claves_agenda
            if odontologo_id and fecha
        },
        key=lambda clave: (clave[0], clave[1]),
    )

    if not claves:
        return []

    BloqueoAgendaOdontologo.objects.bulk_create(
        [
            BloqueoAgendaOdontologo(odontologo_id=odontologo_id, fecha=fecha)
            for odontologo_id, fecha in claves
        ],
        ignore_conflicts=True,
    )

    filtro = Q()

    for odontologo_id, fecha in claves:
        filtro |= Q(odontologo_id=odontologo_id, fecha=fecha)

    return list(
        BloqueoAgendaOdontologo.objects.select_for_update()
        .filter(filtro)
        .order_by("odontologo_id", "fecha")
    )


class ExcepcionAgenda(models.Model):
    class Tipo(models.TextChoices):
        VACACIONES = "vacaciones", "Vacaciones"
        FERIADO = "feriado", "Feriado"
        CAPACITACION = "capacitacion", "Capacitación"
        AUSENCIA_PERSONAL = "ausencia_personal", "Ausencia personal"
        CIERRE_CONSULTORIO = "cierre_consultorio", "Cierre del consultorio"
        BLOQUEO_PARCIAL = "bloqueo_parcial", "Bloqueo parcial"
        OTRO = "otro", "Otro"

    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="excepciones_agenda",
        help_text="Dejar vacío para bloquear todo el consultorio.",
    )
    fecha_desde = models.DateField()
    fecha_hasta = models.DateField()
    todo_el_dia = models.BooleanField(default=True)
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    motivo = models.CharField(max_length=200)
    mensaje_publico = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Mensaje opcional para indicar el motivo operativo sin exponer detalles internos."
        ),
    )
    activo = models.BooleanField(default=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="excepciones_agenda_creadas",
    )
    actualizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="excepciones_agenda_actualizadas",
    )
    desactivada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="excepciones_agenda_desactivadas",
    )
    desactivada_en = models.DateTimeField(null=True, blank=True, editable=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-activo", "fecha_desde", "hora_inicio", "odontologo"]
        verbose_name = "Excepción de agenda"
        verbose_name_plural = "Excepciones de agenda"
        indexes = [
            models.Index(fields=["activo", "fecha_desde", "fecha_hasta"]),
            models.Index(fields=["odontologo", "activo", "fecha_desde", "fecha_hasta"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_hasta__gte=models.F("fecha_desde")),
                name="excepcion_agenda_fecha_hasta_gte_desde",
            ),
            models.CheckConstraint(
                condition=(
                    Q(todo_el_dia=True, hora_inicio__isnull=True, hora_fin__isnull=True)
                    | Q(
                        todo_el_dia=False,
                        hora_inicio__isnull=False,
                        hora_fin__isnull=False,
                        hora_fin__gt=models.F("hora_inicio"),
                    )
                ),
                name="excepcion_agenda_horario_consistente",
            ),
        ]

    @property
    def es_global(self):
        return self.odontologo_id is None

    @property
    def alcance_display(self):
        return "Todo el consultorio" if self.es_global else str(self.odontologo)

    @property
    def horario_display(self):
        if self.todo_el_dia:
            return "Todo el día"

        return f"{self.hora_inicio:%H:%M} a {self.hora_fin:%H:%M}"

    def clean(self):
        errors = {}

        if self.fecha_desde and self.fecha_hasta:
            if self.fecha_hasta < self.fecha_desde:
                errors["fecha_hasta"] = (
                    "La fecha hasta debe ser posterior o igual a la fecha desde."
                )

            if (self.fecha_hasta - self.fecha_desde).days > 365:
                errors["fecha_hasta"] = "El rango no puede superar 366 días corridos."

            if self.activo and self.fecha_hasta < timezone.localdate():
                errors["fecha_hasta"] = (
                    "No se pueden crear excepciones activas totalmente vencidas."
                )

        if self.todo_el_dia:
            self.hora_inicio = None
            self.hora_fin = None
        elif not self.hora_inicio or not self.hora_fin:
            errors["hora_inicio"] = "Indicá hora de inicio y fin para un bloqueo parcial."
        elif self.hora_inicio >= self.hora_fin:
            errors["hora_fin"] = "La hora de fin debe ser posterior al inicio."

        if self.odontologo_id and self.odontologo and not self.odontologo.activo:
            errors["odontologo"] = "No se pueden cargar excepciones para odontólogos inactivos."

        if not errors and self.activo:
            self._validar_duplicado_activo(errors)

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Las excepciones de agenda no se borran; se desactivan para conservar auditoría.",
            self,
        )

    def bloquea_intervalo(self, fecha, hora_inicio, hora_fin):
        if not self.activo or fecha < self.fecha_desde or fecha > self.fecha_hasta:
            return False

        if self.todo_el_dia:
            return True

        inicio = datetime.combine(fecha, hora_inicio)
        fin = datetime.combine(fecha, hora_fin)
        inicio_bloqueo = datetime.combine(fecha, self.hora_inicio)
        fin_bloqueo = datetime.combine(fecha, self.hora_fin)
        return inicio < fin_bloqueo and fin > inicio_bloqueo

    def _validar_duplicado_activo(self, errors):
        duplicadas = ExcepcionAgenda.objects.filter(
            activo=True,
            tipo=self.tipo,
            odontologo_id=self.odontologo_id,
            fecha_desde=self.fecha_desde,
            fecha_hasta=self.fecha_hasta,
            todo_el_dia=self.todo_el_dia,
            hora_inicio=self.hora_inicio,
            hora_fin=self.hora_fin,
        )

        if self.pk:
            duplicadas = duplicadas.exclude(pk=self.pk)

        if duplicadas.exists():
            errors["fecha_desde"] = "Ya existe una excepción activa equivalente."

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.alcance_display} ({self.fecha_desde:%d/%m/%Y})"


class Turno(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CONFIRMADO = "confirmado", "Confirmado"
        CANCELADO = "cancelado", "Cancelado"

    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.PROTECT,
        related_name="turnos",
    )
    odontologo = models.ForeignKey(
        Odontologo,
        on_delete=models.PROTECT,
        related_name="turnos",
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    duracion_minutos = models.PositiveSmallIntegerField(default=30)
    motivo = models.CharField(max_length=200, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    notas = models.TextField(blank=True)
    motivo_cancelacion_paciente = models.TextField(blank=True)
    google_calendar_event_id = models.CharField(max_length=255, blank=True)
    recordatorio_email_enviado_en = models.DateTimeField(null=True, blank=True)
    recordatorio_email_ultimo_error = models.TextField(blank=True)
    version_publica = models.UUIDField(default=uuid4, editable=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fecha", "hora_inicio"]
        verbose_name = "Turno"
        verbose_name_plural = "Turnos"
        indexes = [
            models.Index(fields=["fecha", "hora_inicio"]),
            models.Index(fields=["odontologo", "fecha"]),
            models.Index(fields=["estado"]),
        ]

    @property
    def fecha_hora_inicio(self):
        return datetime.combine(self.fecha, self.hora_inicio)

    @property
    def fecha_hora_inicio_local(self):
        return timezone.make_aware(
            self.fecha_hora_inicio,
            timezone.get_current_timezone(),
        )

    @property
    def fecha_hora_fin(self):
        return self.fecha_hora_inicio + timedelta(minutes=self.duracion_minutos)

    @property
    def hora_fin(self):
        return self.fecha_hora_fin.time()

    @property
    def sincronizado_con_google_calendar(self):
        return bool(self.google_calendar_event_id)

    @property
    def recordatorio_email_enviado(self):
        return self.recordatorio_email_enviado_en is not None

    @property
    def tiene_solicitud_publica(self):
        return hasattr(self, "solicitud_publica")

    @property
    def solicitud_publica_revisada(self):
        if not self.tiene_solicitud_publica:
            return False

        return not self.solicitud_publica.esta_pendiente_revision

    @property
    def tiene_datos_publicos_pendientes_revision(self):
        if not self.tiene_solicitud_publica:
            return False

        return self.solicitud_publica.esta_pendiente_revision

    def clean(self):
        errors = {}

        if self.duracion_minutos <= 0:
            errors["duracion_minutos"] = "La duración debe ser mayor a 0."

        if not errors and self.fecha and self.hora_inicio and self.odontologo_id:
            if self.estado != self.Estado.CANCELADO:
                self._validar_paciente_activo(errors)
                self._validar_odontologo_activo(errors)
                self._validar_disponibilidad(errors)
                self._validar_excepciones_agenda(errors)
                self._validar_solapamiento(errors)

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            bloquear_agendas_de_turnos(self._obtener_claves_bloqueo_agenda())
            self._rotar_version_publica_si_corresponde(kwargs)
            self.full_clean()
            super().save(*args, **kwargs)

        self._asegurar_asociacion_paciente_odontologo()

    def _rotar_version_publica_si_corresponde(self, save_kwargs):
        if not self.pk:
            return

        campos_relevantes = {
            "paciente_id",
            "odontologo_id",
            "fecha",
            "hora_inicio",
            "duracion_minutos",
            "estado",
        }
        turno_original = Turno.objects.filter(pk=self.pk).values(*campos_relevantes).first()

        if not turno_original:
            return

        if not any(getattr(self, campo) != turno_original[campo] for campo in campos_relevantes):
            return

        self.version_publica = uuid4()

        update_fields = save_kwargs.get("update_fields")

        if update_fields is not None:
            update_fields = set(update_fields)
            update_fields.add("version_publica")
            update_fields.add("actualizado_en")
            save_kwargs["update_fields"] = update_fields

    def _obtener_claves_bloqueo_agenda(self):
        claves = []

        if self.odontologo_id and self.fecha:
            claves.append((self.odontologo_id, self.fecha))

        if self.pk:
            turno_original = (
                Turno.objects.filter(pk=self.pk).values("odontologo_id", "fecha").first()
            )

            if turno_original:
                claves.append(
                    (
                        turno_original["odontologo_id"],
                        turno_original["fecha"],
                    )
                )

        return claves

    def _asegurar_asociacion_paciente_odontologo(self):
        if self.paciente_id and not self.paciente.activo:
            return

        from pacientes.services import asegurar_paciente_asociado_a_odontologo

        asegurar_paciente_asociado_a_odontologo(
            self.paciente,
            self.odontologo,
            motivo="Turno creado",
        )

    def _validar_odontologo_activo(self, errors):
        if not self.odontologo.activo:
            errors["odontologo"] = "No se pueden cargar turnos para un odontólogo inactivo."

    def _validar_paciente_activo(self, errors):
        if self.paciente_id and not self.paciente.activo:
            errors["paciente"] = "No se pueden cargar turnos activos para pacientes archivados."

    def _validar_disponibilidad(self, errors):
        if self.fecha_hora_fin.date() != self.fecha:
            errors["duracion_minutos"] = "El turno debe terminar el mismo día."
            return

        disponibilidad = DisponibilidadOdontologo.objects.filter(
            odontologo=self.odontologo,
            dia_semana=self.fecha.weekday(),
            activo=True,
            hora_inicio__lte=self.hora_inicio,
            hora_fin__gte=self.hora_fin,
        ).exists()

        if not disponibilidad:
            errors["hora_inicio"] = "El odontólogo no atiende en ese día y horario."

    def _validar_excepciones_agenda(self, errors):
        if not self._requiere_validacion_excepciones_agenda():
            return

        try:
            from .excepciones import validar_intervalo_sin_excepcion

            validar_intervalo_sin_excepcion(
                self.odontologo,
                self.fecha,
                self.hora_inicio,
                self.hora_fin,
            )
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                errors.update(error.message_dict)
            else:
                errors["hora_inicio"] = error.messages

    def _requiere_validacion_excepciones_agenda(self):
        if not self.pk:
            return True

        campos_agenda = (
            "odontologo_id",
            "fecha",
            "hora_inicio",
            "duracion_minutos",
            "estado",
        )
        original = Turno.objects.filter(pk=self.pk).values(*campos_agenda).first()

        if not original:
            return True

        return any(getattr(self, campo) != original[campo] for campo in campos_agenda)

    def _validar_solapamiento(self, errors):
        if self.estado == self.Estado.CANCELADO:
            return

        turnos_activos = Turno.objects.filter(
            odontologo=self.odontologo,
            fecha=self.fecha,
            estado__in=[self.Estado.PENDIENTE, self.Estado.CONFIRMADO],
        )

        if self.pk:
            turnos_activos = turnos_activos.exclude(pk=self.pk)

        for turno in turnos_activos:
            if (
                self.fecha_hora_inicio < turno.fecha_hora_fin
                and self.fecha_hora_fin > turno.fecha_hora_inicio
            ):
                errors["hora_inicio"] = "Ya existe un turno para ese odontólogo en ese horario."
                return

    def __str__(self):
        return f"{self.fecha} {self.hora_inicio} - {self.paciente}"


class DesafioAccesoPublicoTurnos(models.Model):
    class Canal(models.TextChoices):
        EMAIL = "email", "Email"
        FICTICIO = "ficticio", "Ficticio"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="desafios_acceso_turnos",
    )
    canal = models.CharField(max_length=20, choices=Canal.choices, default=Canal.EMAIL)
    codigo_hash = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField()
    validado_en = models.DateTimeField(null=True, blank=True)
    invalidado_en = models.DateTimeField(null=True, blank=True)
    intentos_fallidos = models.PositiveSmallIntegerField(default=0)
    cantidad_envios = models.PositiveSmallIntegerField(default=0)
    ultimo_envio_en = models.DateTimeField(null=True, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    dni_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Desafío de acceso público a turnos"
        verbose_name_plural = "Desafíos de acceso público a turnos"
        indexes = [
            models.Index(fields=["expira_en"]),
            models.Index(fields=["paciente", "invalidado_en", "validado_en"]),
            models.Index(fields=["dni_hash", "creado_en"]),
        ]

    @property
    def esta_activo(self):
        return (
            self.invalidado_en is None
            and self.validado_en is None
            and self.expira_en > timezone.now()
        )

    def invalidar(self, momento=None):
        self.invalidado_en = momento or timezone.now()

    def __str__(self):
        return f"Desafío público {self.id}"


class AccionPublicaTurno(models.Model):
    class TipoAccion(models.TextChoices):
        CANCELAR = "cancelar", "Cancelar"
        REPROGRAMAR = "reprogramar", "Reprogramar"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    turno = models.ForeignKey(
        Turno,
        on_delete=models.CASCADE,
        related_name="acciones_publicas",
    )
    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.CASCADE,
        related_name="acciones_publicas_turnos",
    )
    tipo_accion = models.CharField(max_length=20, choices=TipoAccion.choices)
    token_hash = models.TextField()
    version_turno = models.UUIDField()
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField()
    utilizado_en = models.DateTimeField(null=True, blank=True)
    revocado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Acción pública de turno"
        verbose_name_plural = "Acciones públicas de turnos"
        indexes = [
            models.Index(fields=["turno", "tipo_accion", "revocado_en", "utilizado_en"]),
            models.Index(fields=["paciente", "expira_en"]),
            models.Index(fields=["expira_en"]),
        ]

    @property
    def esta_activa(self):
        return (
            self.utilizado_en is None
            and self.revocado_en is None
            and self.expira_en > timezone.now()
        )

    def __str__(self):
        return f"{self.get_tipo_accion_display()} - {self.turno_id}"


class SolicitudTurnoPublica(models.Model):
    class EstadoRevision(models.TextChoices):
        SIN_DIFERENCIAS = "sin_diferencias", "Sin diferencias"
        PENDIENTE = "pendiente", "Pendiente de revision"
        REVISADA_SIN_CAMBIOS = "revisada_sin_cambios", "Revisada sin cambios"
        CAMBIOS_APLICADOS = "cambios_aplicados", "Cambios aplicados"
        RECHAZADA = "rechazada", "Descartada"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    turno = models.OneToOneField(
        Turno,
        on_delete=models.CASCADE,
        related_name="solicitud_publica",
        null=True,
        blank=True,
    )
    paciente = models.ForeignKey(
        "pacientes.Paciente",
        on_delete=models.PROTECT,
        related_name="solicitudes_turno_publicas",
    )
    documento_enviado = models.CharField(max_length=20)
    nombre_enviado = models.CharField(max_length=100)
    apellido_enviado = models.CharField(max_length=100)
    telefono_enviado = models.CharField(max_length=30)
    email_enviado = models.EmailField(blank=True)
    motivo_enviado = models.CharField(max_length=200, blank=True)
    paciente_existente = models.BooleanField(default=False)
    requiere_revision = models.BooleanField(default=False)
    diferencias_detectadas = models.JSONField(default=dict, blank=True)
    estado_revision = models.CharField(
        max_length=30,
        choices=EstadoRevision.choices,
        default=EstadoRevision.PENDIENTE,
    )
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitudes_publicas_revisadas",
    )
    revisada_en = models.DateTimeField(null=True, blank=True)
    observaciones_revision = models.TextField(blank=True)
    campos_actualizados = models.JSONField(default=list, blank=True)
    campos_descartados = models.JSONField(default=list, blank=True)
    notificacion_contacto_existente_en = models.DateTimeField(null=True, blank=True)
    notificacion_contacto_existente_error = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Solicitud publica de turno"
        verbose_name_plural = "Solicitudes publicas de turnos"
        indexes = [
            models.Index(fields=["estado_revision", "creado_en"]),
            models.Index(fields=["requiere_revision", "creado_en"]),
            models.Index(fields=["paciente", "creado_en"]),
            models.Index(fields=["turno"]),
        ]

    @property
    def esta_pendiente_revision(self):
        return self.estado_revision == self.EstadoRevision.PENDIENTE

    @property
    def tiene_turno(self):
        return self.turno_id is not None

    @property
    def es_alerta_administrativa(self):
        return self.esta_pendiente_revision and self.turno_id is None

    def __str__(self):
        return f"Solicitud publica {self.creado_en:%Y-%m-%d} - {self.paciente}"


class GoogleCalendarConexion(models.Model):
    odontologo = models.OneToOneField(
        Odontologo,
        on_delete=models.CASCADE,
        related_name="google_calendar_conexion",
    )
    calendar_id = models.CharField(max_length=255, default="primary")
    access_token = EncryptedTextField(blank=True)
    refresh_token = EncryptedTextField(blank=True)
    token_type = models.CharField(max_length=50, default="Bearer")
    scopes = models.JSONField(default=list, blank=True)
    token_expira_en = models.DateTimeField(null=True, blank=True)
    activa = models.BooleanField(default=True)
    ultimo_error = models.TextField(blank=True)
    sincronizado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["odontologo"]
        verbose_name = "Conexión con Google Calendar"
        verbose_name_plural = "Conexiones con Google Calendar"
        indexes = [
            models.Index(fields=["activa"]),
        ]

    @property
    def esta_conectada(self):
        return self.activa and bool(self.refresh_token)

    @property
    def access_token_expirado(self):
        if not self.token_expira_en:
            return False

        return self.token_expira_en <= timezone.now()

    @property
    def necesita_renovar_access_token(self):
        return self.esta_conectada and (not self.access_token or self.access_token_expirado)

    @property
    def ultimo_error_para_usuario(self):
        return normalizar_error_google_calendar_para_usuario(self.ultimo_error)

    def clean(self):
        errors = {}

        if not isinstance(self.scopes, list):
            errors["scopes"] = "Los permisos OAuth deben guardarse como una lista."

        if errors:
            raise ValidationError(errors)

    def registrar_tokens(
        self,
        *,
        access_token,
        refresh_token="",
        token_expira_en=None,
        scopes=None,
        token_type="Bearer",
    ):
        self.access_token = access_token
        self.token_type = token_type or "Bearer"
        self.token_expira_en = token_expira_en
        self.ultimo_error = ""

        if refresh_token:
            self.refresh_token = refresh_token

        if scopes is not None:
            self.scopes = list(scopes)

    def registrar_error(self, mensaje):
        self.ultimo_error = mensaje
        self.save(update_fields=["ultimo_error", "actualizado_en"])

    def desconectar(self):
        self.access_token = ""
        self.refresh_token = ""
        self.token_expira_en = None
        self.scopes = []
        self.activa = False
        self.ultimo_error = ""
        self.save(
            update_fields=[
                "access_token",
                "refresh_token",
                "token_expira_en",
                "scopes",
                "activa",
                "ultimo_error",
                "actualizado_en",
            ]
        )

    def marcar_sincronizada(self):
        self.sincronizado_en = timezone.now()
        self.ultimo_error = ""
        self.save(update_fields=["sincronizado_en", "ultimo_error", "actualizado_en"])

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Google Calendar - {self.odontologo}"


def normalizar_error_google_calendar_para_usuario(mensaje):
    if not mensaje:
        return ""

    mensaje_normalizado = mensaje.lower()

    if any(
        patron in mensaje_normalizado
        for patron in (
            "token",
            "oauth",
            "credential",
            "credencial",
            "unauthorized",
            "invalid_grant",
            "401",
            "403",
            "refresh token",
            "access token",
        )
    ):
        return (
            "No se pudo autorizar la conexión con Google Calendar. "
            "Revisá la conexión del odontólogo y volvé a intentar."
        )

    if "conexion activa" in mensaje_normalizado:
        return (
            "El odontólogo no tiene una conexión activa con Google Calendar. "
            "Conectá Google Calendar y volvé a intentar."
        )

    if any(patron in mensaje_normalizado for patron in ("not found", "no se encontro", "404")):
        return (
            "No se encontró el evento en Google Calendar. "
            "Podés reintentar la sincronización para crear o actualizar el evento."
        )

    if any(
        patron in mensaje_normalizado for patron in ("timeout", "connection", "conectar", "red")
    ):
        return "No se pudo conectar con Google Calendar. " "Reintenta en unos minutos."

    return (
        "No se pudo sincronizar el turno con Google Calendar. "
        "Revisá la conexión del odontólogo o reintentá la sincronización."
    )
