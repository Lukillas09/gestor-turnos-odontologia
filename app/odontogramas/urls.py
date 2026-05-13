from django.urls import path

from .views import EstadoDentalCreateView, OdontogramaDetailView

app_name = "odontogramas"

urlpatterns = [
    path(
        "pacientes/<int:paciente_pk>/",
        OdontogramaDetailView.as_view(),
        name="detalle_paciente",
    ),
    path(
        "pacientes/<int:paciente_pk>/estados/",
        EstadoDentalCreateView.as_view(),
        name="crear_estado",
    ),
]
