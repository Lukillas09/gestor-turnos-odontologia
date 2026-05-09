from django.urls import path

from .views import (
    HistoriaClinicaCreateView,
    HistoriaClinicaDetailView,
    HistoriaClinicaListView,
    HistoriaClinicaUpdateView,
)

app_name = "historias"

urlpatterns = [
    path(
        "pacientes/<int:paciente_pk>/",
        HistoriaClinicaListView.as_view(),
        name="lista_paciente",
    ),
    path(
        "pacientes/<int:paciente_pk>/nueva/",
        HistoriaClinicaCreateView.as_view(),
        name="crear",
    ),
    path("<int:pk>/", HistoriaClinicaDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", HistoriaClinicaUpdateView.as_view(), name="editar"),
]
