from django.urls import path

from .views import TurnoCreateView, TurnoListView

app_name = "turnos"

urlpatterns = [
    path("", TurnoListView.as_view(), name="lista"),
    path("nuevo/", TurnoCreateView.as_view(), name="crear"),
]
