from django.urls import path

from .views import (
    HistoriaClinicaAdjuntoDownloadView,
    HistoriaClinicaCreateView,
    HistoriaClinicaDetailView,
    HistoriaClinicaEnmiendaCreateView,
    HistoriaClinicaEnmiendaDetailView,
    HistoriaClinicaExportView,
    HistoriaClinicaFinalizarView,
    HistoriaClinicaListView,
    HistoriaClinicaUpdateView,
    HistoriaClinicaVerificarIntegridadView,
    HistoriaClinicaVersionDetailView,
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
    path("<int:pk>/finalizar/", HistoriaClinicaFinalizarView.as_view(), name="finalizar"),
    path(
        "<int:pk>/enmiendas/nueva/",
        HistoriaClinicaEnmiendaCreateView.as_view(),
        name="crear_enmienda",
    ),
    path(
        "versiones/<int:pk>/",
        HistoriaClinicaVersionDetailView.as_view(),
        name="detalle_version",
    ),
    path(
        "enmiendas/<int:pk>/",
        HistoriaClinicaEnmiendaDetailView.as_view(),
        name="detalle_enmienda",
    ),
    path(
        "<int:pk>/verificar-integridad/",
        HistoriaClinicaVerificarIntegridadView.as_view(),
        name="verificar_integridad",
    ),
    path("<int:pk>/exportar/", HistoriaClinicaExportView.as_view(), name="exportar"),
    path(
        "adjuntos/<int:pk>/descargar/",
        HistoriaClinicaAdjuntoDownloadView.as_view(),
        name="descargar_adjunto",
    ),
]
