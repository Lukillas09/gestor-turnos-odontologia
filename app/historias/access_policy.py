from datetime import timedelta

from django.conf import settings
from django.core.signing import salted_hmac
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from pacientes.models import PacienteOdontologo
from usuarios.roles import obtener_odontologo_del_usuario

from .models import AccesoClinicoAuditoria

EMERGENCY_SESSION_KEY = "acceso_clinico_emergencia"


def datos_clinicos_compartidos_habilitados():
    return bool(getattr(settings, "DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS", False))


def obtener_estado_acceso_emergencia(request):
    data = request.session.get(EMERGENCY_SESSION_KEY)

    if not isinstance(data, dict):
        return None

    if data.get("usuario_id") != request.user.pk:
        finalizar_acceso_clinico_emergencia(request)
        return None

    expira_en = parse_datetime(data.get("expira_en") or "")

    if not expira_en:
        finalizar_acceso_clinico_emergencia(request)
        return None

    if timezone.is_naive(expira_en):
        expira_en = timezone.make_aware(expira_en, timezone.get_current_timezone())

    if expira_en <= timezone.now():
        finalizar_acceso_clinico_emergencia(request)
        return None

    return {
        "paciente_id": data.get("paciente_id"),
        "usuario_id": data.get("usuario_id"),
        "motivo": data.get("motivo", ""),
        "iniciado_en": data.get("iniciado_en"),
        "expira_en": expira_en,
    }


def iniciar_acceso_clinico_emergencia(request, paciente, motivo):
    ahora = timezone.now()
    segundos = int(getattr(settings, "ACCESO_CLINICO_EMERGENCIA_SECONDS", 900))
    expira_en = ahora + timedelta(seconds=segundos)
    request.session[EMERGENCY_SESSION_KEY] = {
        "paciente_id": paciente.pk,
        "usuario_id": request.user.pk,
        "motivo": motivo.strip(),
        "iniciado_en": ahora.isoformat(),
        "expira_en": expira_en.isoformat(),
    }
    request.session.modified = True
    return expira_en


def finalizar_acceso_clinico_emergencia(request):
    request.session.pop(EMERGENCY_SESSION_KEY, None)
    request.session.modified = True


def usuario_puede_iniciar_acceso_emergencia(usuario):
    return usuario.is_authenticated and usuario.is_superuser


def puede_ver_datos_clinicos_de_paciente(usuario, paciente, request=None):
    return bool(obtener_politica_lectura(usuario, paciente, request=request))


def puede_modificar_datos_clinicos_de_paciente(usuario, paciente):
    return bool(obtener_politica_escritura(usuario, paciente))


def puede_editar_ficha_odontologica(usuario, paciente):
    return puede_modificar_datos_clinicos_de_paciente(usuario, paciente)


def puede_crear_historia_de_paciente(usuario, paciente):
    return puede_modificar_datos_clinicos_de_paciente(usuario, paciente)


def puede_editar_historia_clinica(usuario, historia):
    return historia.puede_editarse and _puede_escribir_historia(usuario, historia)


def puede_enmendar_historia_clinica(usuario, historia):
    return (
        not historia.borrador
        and historia.bloqueada_para_edicion
        and _puede_escribir_historia(usuario, historia)
    )


def _puede_escribir_historia(usuario, historia):
    if not usuario.is_authenticated:
        return False

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return False

    if not historia.paciente.activo:
        return False

    return historia.odontologo_id == odontologo.pk and _paciente_asociado_a_odontologo(
        historia.paciente_id, odontologo.pk
    )


def obtener_politica_lectura(usuario, paciente, request=None):
    if not usuario.is_authenticated or not paciente:
        return ""

    if request and _emergencia_valida_para_paciente(request, paciente):
        return AccesoClinicoAuditoria.Politica.EMERGENCIA

    if not paciente.activo:
        return ""

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return ""

    if _paciente_asociado_a_odontologo(paciente.pk, odontologo.pk):
        return AccesoClinicoAuditoria.Politica.ASOCIACION_ACTIVA

    if datos_clinicos_compartidos_habilitados():
        return AccesoClinicoAuditoria.Politica.COMPARTIDO

    return ""


def obtener_politica_escritura(usuario, paciente):
    if not usuario.is_authenticated or not paciente or not paciente.activo:
        return ""

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return ""

    if _paciente_asociado_a_odontologo(paciente.pk, odontologo.pk):
        return AccesoClinicoAuditoria.Politica.ASOCIACION_ACTIVA

    return ""


def limitar_pacientes_clinicos_por_usuario(queryset, usuario, lectura=True):
    if not usuario.is_authenticated:
        return queryset.none()

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return queryset.none()

    queryset = queryset.filter(activo=True)

    if lectura and datos_clinicos_compartidos_habilitados():
        return queryset

    filtro = Q(
        odontologos_asociados__odontologo=odontologo,
        odontologos_asociados__activo=True,
    )

    return queryset.filter(filtro).distinct()


def limitar_pacientes_clinicos_para_request(queryset, request, lectura=True):
    base = limitar_pacientes_clinicos_por_usuario(
        queryset,
        request.user,
        lectura=lectura,
    )
    emergencia = obtener_estado_acceso_emergencia(request) if lectura else None

    if not emergencia:
        return base

    return queryset.filter(Q(pk__in=base.values("pk")) | Q(pk=emergencia["paciente_id"]))


def limitar_historias_clinicas_por_usuario(queryset, usuario, lectura=True):
    if not usuario.is_authenticated:
        return queryset.none()

    odontologo = obtener_odontologo_del_usuario(usuario)

    if odontologo is None:
        return queryset.none()

    queryset = queryset.filter(paciente__activo=True)

    if lectura and datos_clinicos_compartidos_habilitados():
        return queryset

    filtro = Q(
        paciente__odontologos_asociados__odontologo=odontologo,
        paciente__odontologos_asociados__activo=True,
    )

    return queryset.filter(filtro).distinct()


def limitar_historias_clinicas_para_request(queryset, request, lectura=True):
    base = limitar_historias_clinicas_por_usuario(queryset, request.user, lectura=lectura)
    emergencia = obtener_estado_acceso_emergencia(request) if lectura else None

    if not emergencia:
        return base

    return queryset.filter(Q(pk__in=base.values("pk")) | Q(paciente_id=emergencia["paciente_id"]))


def registrar_evento_acceso_clinico(
    *,
    request=None,
    usuario=None,
    accion,
    resultado,
    politica="",
    paciente=None,
    historia=None,
    adjunto=None,
    identificador_solicitado="",
    motivo="",
):
    usuario = usuario or (request.user if request and request.user.is_authenticated else None)
    request_path = request.path[:255] if request else ""
    request_method = request.method[:12] if request else ""
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""
    ip_hash = _hash_ip_cliente(request) if request else ""

    es_emergencia = politica == AccesoClinicoAuditoria.Politica.EMERGENCIA
    es_compartido = politica == AccesoClinicoAuditoria.Politica.COMPARTIDO

    return AccesoClinicoAuditoria.objects.create(
        usuario=usuario,
        paciente=paciente,
        historia=historia,
        adjunto=adjunto,
        identificador_solicitado=str(identificador_solicitado or "")[:120],
        accion=accion,
        resultado=resultado,
        politica=politica or "",
        motivo=(motivo or "")[:1000],
        ruta=request_path,
        metodo=request_method,
        ip_hash=ip_hash,
        user_agent=user_agent,
        es_emergencia=es_emergencia,
        es_acceso_compartido=es_compartido,
    )


def _emergencia_valida_para_paciente(request, paciente):
    if not usuario_puede_iniciar_acceso_emergencia(request.user):
        return False

    estado = obtener_estado_acceso_emergencia(request)
    return bool(estado and estado.get("paciente_id") == paciente.pk)


def _paciente_asociado_a_odontologo(paciente_id, odontologo_id):
    return PacienteOdontologo.objects.filter(
        paciente_id=paciente_id,
        odontologo_id=odontologo_id,
        activo=True,
    ).exists()


def _hash_ip_cliente(request):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
        "REMOTE_ADDR", ""
    )

    if not ip:
        return ""

    return salted_hmac("auditoria-clinica-ip", ip).hexdigest()
