from django.urls import path
from . import views

app_name = "trading"

urlpatterns = [
    path("", views.trading_index, name="index"),
    path("start/", views.start_session, name="start"),
    path("start-preset/", views.start_preset, name="start_preset"),
    path("stop/<int:pk>/", views.stop_session, name="stop"),
    path("status/<int:pk>/", views.session_status, name="status"),
]
