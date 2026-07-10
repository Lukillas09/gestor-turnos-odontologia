from django.urls import path

from .views import ConfiguracionConsultorioView

app_name = "consultorio"

urlpatterns = [
    path("consultorio/", ConfiguracionConsultorioView.as_view(), name="configuracion"),
]
