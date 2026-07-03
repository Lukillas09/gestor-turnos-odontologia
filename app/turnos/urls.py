from django.urls import path

from .views import (
    AgendaDiaView,
    AgendaSemanaView,
    GoogleCalendarCallbackView,
    GoogleCalendarConectarView,
    GoogleCalendarConexionView,
    GoogleCalendarDesconectarView,
    HorariosDisponiblesJsonView,
    SolicitudTurnoPublicaDatosView,
    SolicitudTurnoPublicaHorariosView,
    SolicitudTurnoPublicaOkView,
    SolicitudTurnoPublicaListView,
    SolicitudTurnoPublicaRevisionView,
    SolicitudTurnoPublicaView,
    TurnoCancelView,
    TurnoConfirmView,
    TurnoCreateView,
    TurnoDetailView,
    TurnoListView,
    TurnoReprogramView,
    TurnoReintentarSincronizacionGoogleCalendarView,
    TurnoUpdateView,
)
from .public_access.views import (
    CancelarTurnoPublicoSeguroView,
    CerrarAccesoPublicoTurnosView,
    HorariosReprogramacionPublicaJsonView,
    MisTurnosPublicoView,
    ReprogramarTurnoPublicoSeguroView,
    SolicitarAccesoPublicoTurnosView,
    VerificarAccesoPublicoTurnosView,
)

app_name = "turnos"

urlpatterns = [
    path("", TurnoListView.as_view(), name="lista"),
    path("nuevo/", TurnoCreateView.as_view(), name="crear"),
    path(
        "horarios-disponibles/",
        HorariosDisponiblesJsonView.as_view(),
        name="horarios_disponibles",
    ),
    path("solicitar/", SolicitudTurnoPublicaView.as_view(), name="solicitud_publica"),
    path(
        "solicitar/horarios/",
        SolicitudTurnoPublicaHorariosView.as_view(),
        name="solicitud_publica_horarios",
    ),
    path(
        "solicitar/datos/",
        SolicitudTurnoPublicaDatosView.as_view(),
        name="solicitud_publica_datos",
    ),
    path(
        "solicitar/gracias/",
        SolicitudTurnoPublicaOkView.as_view(),
        name="solicitud_publica_ok",
    ),
    path(
        "solicitudes-publicas/",
        SolicitudTurnoPublicaListView.as_view(),
        name="solicitudes_publicas",
    ),
    path(
        "solicitudes-publicas/<uuid:pk>/",
        SolicitudTurnoPublicaRevisionView.as_view(),
        name="solicitud_publica_revision",
    ),
    path(
        "mis-turnos/solicitar-acceso/",
        SolicitarAccesoPublicoTurnosView.as_view(),
        name="acceso_publico_solicitar",
    ),
    path(
        "mis-turnos/verificar/",
        VerificarAccesoPublicoTurnosView.as_view(),
        name="acceso_publico_verificar",
    ),
    path("mis-turnos/", MisTurnosPublicoView.as_view(), name="mis_turnos_publico"),
    path(
        "mis-turnos/cerrar/",
        CerrarAccesoPublicoTurnosView.as_view(),
        name="acceso_publico_cerrar",
    ),
    path(
        "mis-turnos/<uuid:accion_id>/cancelar/",
        CancelarTurnoPublicoSeguroView.as_view(),
        name="mis_turnos_cancelar",
    ),
    path(
        "mis-turnos/<uuid:accion_id>/reprogramar/",
        ReprogramarTurnoPublicoSeguroView.as_view(),
        name="mis_turnos_reprogramar",
    ),
    path(
        "mis-turnos/<uuid:accion_id>/reprogramar/horarios/",
        HorariosReprogramacionPublicaJsonView.as_view(),
        name="mis_turnos_reprogramar_horarios",
    ),
    path("agenda/dia/", AgendaDiaView.as_view(), name="agenda_dia"),
    path("agenda/semana/", AgendaSemanaView.as_view(), name="agenda_semana"),
    path(
        "google-calendar/",
        GoogleCalendarConexionView.as_view(),
        name="google_calendar",
    ),
    path(
        "google-calendar/conectar/",
        GoogleCalendarConectarView.as_view(),
        name="google_calendar_conectar",
    ),
    path(
        "google-calendar/callback/",
        GoogleCalendarCallbackView.as_view(),
        name="google_calendar_callback",
    ),
    path(
        "google-calendar/desconectar/",
        GoogleCalendarDesconectarView.as_view(),
        name="google_calendar_desconectar",
    ),
    path("<int:pk>/", TurnoDetailView.as_view(), name="detalle"),
    path("<int:pk>/reprogramar/", TurnoReprogramView.as_view(), name="reprogramar"),
    path("<int:pk>/editar/", TurnoUpdateView.as_view(), name="editar"),
    path("<int:pk>/confirmar/", TurnoConfirmView.as_view(), name="confirmar"),
    path("<int:pk>/cancelar/", TurnoCancelView.as_view(), name="cancelar"),
    path(
        "<int:pk>/google-calendar/reintentar/",
        TurnoReintentarSincronizacionGoogleCalendarView.as_view(),
        name="reintentar_google_calendar",
    ),
]
