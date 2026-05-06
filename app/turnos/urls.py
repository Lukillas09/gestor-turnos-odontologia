from django.urls import path

from .views import (
    AgendaDiaView,
    AgendaSemanaView,
    SolicitudTurnoPublicaOkView,
    SolicitudTurnoPublicaView,
    TurnoCancelView,
    TurnoConfirmView,
    TurnoCreateView,
    TurnoDetailView,
    TurnoListView,
    TurnoUpdateView,
)

app_name = "turnos"

urlpatterns = [
    path("", TurnoListView.as_view(), name="lista"),
    path("nuevo/", TurnoCreateView.as_view(), name="crear"),
    path("solicitar/", SolicitudTurnoPublicaView.as_view(), name="solicitud_publica"),
    path(
        "solicitar/gracias/",
        SolicitudTurnoPublicaOkView.as_view(),
        name="solicitud_publica_ok",
    ),
    path("agenda/dia/", AgendaDiaView.as_view(), name="agenda_dia"),
    path("agenda/semana/", AgendaSemanaView.as_view(), name="agenda_semana"),
    path("<int:pk>/", TurnoDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", TurnoUpdateView.as_view(), name="editar"),
    path("<int:pk>/confirmar/", TurnoConfirmView.as_view(), name="confirmar"),
    path("<int:pk>/cancelar/", TurnoCancelView.as_view(), name="cancelar"),
]
