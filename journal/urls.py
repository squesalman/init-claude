from django.urls import path

from journal import views

urlpatterns = [
    path("imports/", views.imports, name="imports"),
    path("imports/<int:pk>/", views.import_detail, name="import_detail"),
    path("imports/<int:pk>/delete/", views.import_delete, name="import_delete"),
    path("trades/new/", views.trade_add, name="trade_add"),
    path("trades/point-value/", views.trade_point_value, name="trade_point_value"),
    path("trades/<int:pk>/close/", views.trade_close, name="trade_close"),
    path("trades/<int:pk>/journal/", views.trade_journal, name="trade_journal"),
    path("rules/", views.save_rules, name="rules"),
]
