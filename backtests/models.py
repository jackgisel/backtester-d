from django.db import models


class Backtest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    security = models.ForeignKey(
        "securities.Security",
        on_delete=models.CASCADE,
        related_name="backtests",
    )
    strategy_name = models.CharField(max_length=100)
    parameters = models.JSONField()
    start_date = models.DateField()
    end_date = models.DateField()
    initial_capital = models.DecimalField(max_digits=14, decimal_places=2, default=10000)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)

    # Result metrics (null until completed)
    total_return_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    sharpe_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    sortino_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    max_drawdown_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    win_rate_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    profit_factor = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    total_trades = models.IntegerField(null=True)
    avg_trade_duration_minutes = models.DecimalField(
        max_digits=10, decimal_places=2, null=True,
    )

    equity_curve = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"{self.strategy_name} | {self.security.symbol} "
            f"{self.start_date}→{self.end_date} [{self.status}]"
        )


class Trade(models.Model):
    class Direction(models.TextChoices):
        LONG = "long", "Long"
        SHORT = "short", "Short"

    class ExitReason(models.TextChoices):
        STOP_LOSS = "stop_loss", "Stop Loss"
        TAKE_PROFIT = "take_profit", "Take Profit"
        EOD = "eod", "End of Day"
        SIGNAL = "signal", "Signal"

    backtest = models.ForeignKey(
        Backtest, on_delete=models.CASCADE, related_name="trades",
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    entry_timestamp = models.DateTimeField()
    exit_timestamp = models.DateTimeField(null=True, blank=True)
    entry_price = models.DecimalField(max_digits=12, decimal_places=4)
    exit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    shares = models.DecimalField(max_digits=14, decimal_places=6)
    pnl = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    pnl_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    commission = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    exit_reason = models.CharField(
        max_length=20, choices=ExitReason.choices, blank=True,
    )
    duration_minutes = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["entry_timestamp"]

    def __str__(self):
        return f"{self.direction} {self.entry_price}→{self.exit_price} P&L:{self.pnl}"


class OptimizationRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    security = models.ForeignKey(
        "securities.Security",
        on_delete=models.CASCADE,
        related_name="optimization_runs",
    )
    strategy_name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    n_trials = models.IntegerField(default=100)
    objective_metric = models.CharField(max_length=50, default="sharpe_ratio")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)

    study_name = models.CharField(max_length=200, unique=True)

    best_params = models.JSONField(null=True, blank=True)
    best_value = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    trials_data = models.JSONField(null=True, blank=True)

    best_backtest = models.ForeignKey(
        Backtest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimization_source",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Optimize {self.strategy_name} | {self.security.symbol} "
            f"[{self.status}] best={self.best_value}"
        )
