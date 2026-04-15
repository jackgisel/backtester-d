from django.contrib import admin
from .models import Backtest, Trade, OptimizationRun


@admin.register(Backtest)
class BacktestAdmin(admin.ModelAdmin):
    list_display = (
        "strategy_name", "security", "start_date", "end_date",
        "status", "total_return_pct", "sharpe_ratio", "total_trades",
    )
    list_filter = ("strategy_name", "status")
    search_fields = ("security__symbol",)
    raw_id_fields = ("security",)


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        "backtest", "direction", "entry_timestamp", "exit_timestamp",
        "entry_price", "exit_price", "pnl", "exit_reason",
    )
    list_filter = ("direction", "exit_reason")
    raw_id_fields = ("backtest",)


@admin.register(OptimizationRun)
class OptimizationRunAdmin(admin.ModelAdmin):
    list_display = (
        "strategy_name", "security", "n_trials", "objective_metric",
        "status", "best_value",
    )
    list_filter = ("strategy_name", "status")
    search_fields = ("security__symbol",)
    raw_id_fields = ("security", "best_backtest")
