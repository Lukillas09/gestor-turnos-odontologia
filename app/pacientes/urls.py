from django.urls import path

from .views import PacienteCreateView, PacienteDetailView, PacienteListView, PacienteUpdateView

app_name = "pacientes"

urlpatterns = [
    path("", PacienteListView.as_view(), name="lista"),
    path("nuevo/", PacienteCreateView.as_view(), name="crear"),
    path("<int:pk>/", PacienteDetailView.as_view(), name="detalle"),
    path("<int:pk>/editar/", PacienteUpdateView.as_view(), name="editar"),
]
