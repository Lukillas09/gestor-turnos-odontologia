from .models import ConfiguracionAgendaInteligente, TipoTurnoOdontologo, Turno
from .smart_scheduling import ALGORITMO_HORARIO_VERSION


def configuraciones_tipos_publicos(odontologo):
    return (
        TipoTurnoOdontologo.objects.select_related("tipo_turno", "odontologo")
        .filter(
            odontologo=odontologo,
            odontologo__activo=True,
            activo=True,
            reserva_publica=True,
            tipo_turno__activo=True,
            tipo_turno__visible_publicamente=True,
        )
        .order_by("tipo_turno__orden_publico", "tipo_turno__nombre", "pk")
    )


def obtener_configuracion_tipo_publica(odontologo, tipo_turno, *, bloquear=False):
    queryset = configuraciones_tipos_publicos(odontologo)
    if bloquear:
        queryset = queryset.select_for_update(of=("self",))

    tipo_turno_id = getattr(tipo_turno, "pk", tipo_turno)
    if not tipo_turno_id:
        return None
    return queryset.filter(tipo_turno_id=tipo_turno_id).first()


def obtener_o_crear_configuracion_agenda(odontologo):
    return ConfiguracionAgendaInteligente.objects.get_or_create(odontologo=odontologo)[0]


def aplicar_snapshot_publico(turno, configuracion_tipo, candidato):
    turno.tipo_turno = configuracion_tipo.tipo_turno
    turno.tipo_turno_nombre_snapshot = configuracion_tipo.tipo_turno.nombre
    turno.duracion_atencion_minutos = configuracion_tipo.duracion_atencion_minutos
    turno.margen_posterior_minutos_snapshot = configuracion_tipo.margen_posterior_minutos
    turno.duracion_minutos = configuracion_tipo.duracion_bloqueada_minutos
    turno.algoritmo_horario_version = ALGORITMO_HORARIO_VERSION
    turno.clasificacion_horario = candidato.clasificacion
    turno.puntaje_horario = candidato.puntaje
    turno.motivo = configuracion_tipo.tipo_turno.nombre
    return turno


def aplicar_snapshot_interno(turno, configuracion_tipo=None):
    turno.algoritmo_horario_version = ""
    turno.clasificacion_horario = Turno.ClasificacionHorario.INTERNO
    turno.puntaje_horario = None

    if configuracion_tipo is None:
        turno.tipo_turno = None
        turno.tipo_turno_nombre_snapshot = ""
        turno.duracion_atencion_minutos = None
        turno.margen_posterior_minutos_snapshot = 0
        return turno

    turno.tipo_turno = configuracion_tipo.tipo_turno
    turno.tipo_turno_nombre_snapshot = configuracion_tipo.tipo_turno.nombre
    turno.duracion_atencion_minutos = configuracion_tipo.duracion_atencion_minutos
    turno.margen_posterior_minutos_snapshot = configuracion_tipo.margen_posterior_minutos
    turno.duracion_minutos = configuracion_tipo.duracion_bloqueada_minutos
    return turno
