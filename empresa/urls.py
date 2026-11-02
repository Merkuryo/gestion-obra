from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "empresa"

urlpatterns = [
    path("ajustes/", RedirectView.as_view(pattern_name="empresa:ficha"), name="inicio"),
    path("ajustes/empresa/", views.FichaEmpresa.as_view(), name="ficha"),
]
