from django.urls import path

from . import views

app_name = "catalogos"

urlpatterns = [
    path("banco/", views.ListaPartidas.as_view(), name="partidas"),
    path("banco/partidas/nueva/", views.FichaPartida.as_view(), name="partida_nueva"),
    path("banco/partidas/<int:pk>/", views.FichaPartida.as_view(), name="partida"),
    path("banco/recursos/", views.ListaRecursos.as_view(), name="recursos"),
    path("banco/recursos/nuevo/", views.FichaRecurso.as_view(), name="recurso_nuevo"),
    path("banco/recursos/<int:pk>/", views.FichaRecurso.as_view(), name="recurso"),
    path("banco/externos/", views.ListaBancos.as_view(), name="bancos"),
    path("banco/externos/<int:pk>/", views.ContenidoBanco.as_view(), name="banco"),
    path(
        "banco/externos/<int:banco_pk>/partidas/<int:pk>/",
        views.PartidaExterna.as_view(),
        name="partida_externa",
    ),
    path(
        "banco/externos/<int:banco_pk>/partidas/<int:pk>/copiar/",
        views.CopiarPartida.as_view(),
        name="partida_copiar",
    ),
    path("banco/importar/", views.ImportarBC3.as_view(), name="importar_bc3"),
]
