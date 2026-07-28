from django.urls import path
from . import views

urlpatterns = [
    path("", views.ombor, name="ombor"),
    path("export/", views.ombor_export, name="ombor_export"),
    path("prixod/", views.prixod_list, name="prixod_list"),
    path("prixod/yangi/", views.prixod_add, name="prixod_add"),
    path("prixod/<int:pk>/amal/", views.prixod_action, name="prixod_action"),
    path("rasxod/", views.rasxod_list, name="rasxod_list"),
    path("rasxod/yangi/", views.rasxod_add, name="rasxod_add"),
    path("rasxod/<int:pk>/amal/", views.rasxod_action, name="rasxod_action"),
]
