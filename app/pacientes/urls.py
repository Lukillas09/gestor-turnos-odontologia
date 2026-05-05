from django.urls import path

from .views import PacienteCreateView, PacienteListView

app_name = "pacientes"

urlpatterns = [
    path("", PacienteListView.as_view(), name="lista"),
    path("nuevo/", PacienteCreateView.as_view(), name="crear"),
]
