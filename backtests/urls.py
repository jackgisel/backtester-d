from django.urls import path

from . import views

app_name = "backtests"

urlpatterns = [
    path("", views.index, name="index"),
    path("run/", views.run_backtest, name="run"),
    path("results/<int:pk>/", views.backtest_detail, name="detail"),
    path("optimize/", views.run_optimization_view, name="optimize"),
    path("walk-forward/", views.run_walk_forward_view, name="walk_forward"),
    path("cross-validate/", views.run_cross_validation_view, name="cross_validate"),
]
