from django.urls import path

from .views import (
    FichaOdontologicaUpdateView,
    PacienteArchiveView,
    PacienteCreateView,
    PacienteDeleteView,
    PacienteDerivarView,
    PacienteDetailView,
    PacienteEmergenciaClinicaEndView,
    PacienteEmergenciaClinicaStartView,
    PacienteListView,
    PacienteReactivateView,
    PacienteUpdateView,
)

app_name = "pacientes"

urlpatterns = [
    path("", PacienteListView.as_view(), name="lista"),
    path("nuevo/", PacienteCreateView.as_view(), name="crear"),
    path("<int:pk>/", PacienteDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", PacienteUpdateView.as_view(), name="editar"),
    path("<int:pk>/derivar/", PacienteDerivarView.as_view(), name="derivar"),
    path(
        "<int:pk>/ficha-odontologica/",
        FichaOdontologicaUpdateView.as_view(),
        name="ficha_odontologica",
    ),
    path("<int:pk>/archivar/", PacienteArchiveView.as_view(), name="archivar"),
    path("<int:pk>/reactivar/", PacienteReactivateView.as_view(), name="reactivar"),
    path(
        "<int:pk>/emergencia-clinica/",
        PacienteEmergenciaClinicaStartView.as_view(),
        name="emergencia_clinica",
    ),
    path(
        "emergencia-clinica/finalizar/",
        PacienteEmergenciaClinicaEndView.as_view(),
        name="emergencia_clinica_finalizar",
    ),
    path("<int:pk>/borrar/", PacienteDeleteView.as_view(), name="borrar"),
]
