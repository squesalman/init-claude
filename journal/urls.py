from django.urls import path

from journal import views

urlpatterns = [
    path("imports/", views.imports, name="imports"),
    path("imports/<int:pk>/", views.import_detail, name="import_detail"),
    path("imports/<int:pk>/delete/", views.import_delete, name="import_delete"),
]
