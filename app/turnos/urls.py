from django.urls import path

from .views import (
    AgendaDiaView,
    AgendaSemanaView,
    TurnoCancelView,
    TurnoCreateView,
    TurnoDetailView,
    TurnoListView,
    TurnoUpdateView,
)

app_name = "turnos"

urlpatterns = [
    path("", TurnoListView.as_view(), name="lista"),
    path("nuevo/", TurnoCreateView.as_view(), name="crear"),
    path("agenda/dia/", AgendaDiaView.as_view(), name="agenda_dia"),
    path("agenda/semana/", AgendaSemanaView.as_view(), name="agenda_semana"),
    path("<int:pk>/", TurnoDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", TurnoUpdateView.as_view(), name="editar"),
    path("<int:pk>/cancelar/", TurnoCancelView.as_view(), name="cancelar"),
]
