from django.db import models


# Optimized Kelly-weighted configs for the hybrid system
HYBRID_PRESETS = {
    "ORB_HYBRID_50K": {
        "name": "ORB Hybrid $50k",
        "description": "Kelly-weighted ORB on AVGO/GOOGL/CRM/META. Stock + 1DTE options on top signals. 2x margin.",
        "capital": 50000,
        "use_margin": True,
        "margin_multiplier": 2.0,
        "max_trades_per_day": 4,
        "options_on_top_pct": 30,  # top 30% of signals use options
        "symbols": {
            "AVGO": {
                "weight": 0.46,
                "params": {
                    "opening_range_minutes": 10,
                    "use_atr_stops": False,
                    "stop_loss_pct": 3.0,
                    "take_profit_pct": 4.5,
                    "use_trend_filter": True,
                    "entry_cutoff_minutes": 210,
                },
            },
            "GOOGL": {
                "weight": 0.24,
                "params": {
                    "opening_range_minutes": 5,
                    "use_atr_stops": False,
                    "stop_loss_pct": 1.0,
                    "take_profit_pct": 1.5,
                    "use_trend_filter": True,
                    "entry_cutoff_minutes": 90,
                },
            },
            "CRM": {
                "weight": 0.20,
                "params": {
                    "opening_range_minutes": 25,
                    "use_atr_stops": True,
                    "atr_stop_mult": 2.0,
                    "atr_tp_mult": 6.0,
                    "use_trend_filter": True,
                    "entry_cutoff_minutes": 150,
                },
            },
            "META": {
                "weight": 0.10,
                "params": {
                    "opening_range_minutes": 10,
                    "use_atr_stops": True,
                    "atr_stop_mult": 3.0,
                    "atr_tp_mult": 2.0,
                    "use_trend_filter": False,
                    "entry_cutoff_minutes": 60,
                },
            },
        },
    },
}


class LiveSession(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Mode(models.TextChoices):
        STOCK = "stock", "Stock Only"
        HYBRID = "hybrid", "Hybrid (Stock + Options)"

    strategy_name = models.CharField(max_length=100)
    symbols = models.JSONField()  # ["AVGO", "GOOGL", ...]
    parameters = models.JSONField()  # full config including per-symbol params
    capital = models.DecimalField(max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.STOCK)
    use_margin = models.BooleanField(default=False)
    margin_multiplier = models.DecimalField(max_digits=4, decimal_places=1, default=1.0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)

    # Runtime state
    symbol_states = models.JSONField(default=dict, blank=True)
    total_pnl = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    trades_today = models.IntegerField(default=0)
    options_trades_today = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    stopped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def effective_capital(self):
        return float(self.capital) * float(self.margin_multiplier)

    def __str__(self):
        mode_label = "HYBRID" if self.mode == self.Mode.HYBRID else "STOCK"
        return f"{self.strategy_name} [{mode_label}] | {','.join(self.symbols)} [{self.status}]"


class LiveTrade(models.Model):
    class InstrumentType(models.TextChoices):
        STOCK = "stock", "Stock"
        OPTION = "option", "Option"

    session = models.ForeignKey(
        LiveSession, on_delete=models.CASCADE, related_name="trades",
    )
    symbol = models.CharField(max_length=20)
    instrument_type = models.CharField(
        max_length=10, choices=InstrumentType.choices, default=InstrumentType.STOCK,
    )
    direction = models.CharField(max_length=10)
    entry_price = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    exit_price = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    shares = models.DecimalField(max_digits=14, decimal_places=6, null=True)
    contracts = models.IntegerField(null=True, blank=True)  # for options
    option_symbol = models.CharField(max_length=50, blank=True)  # OCC symbol
    pnl = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    exit_reason = models.CharField(max_length=50, blank=True)
    alpaca_order_id = models.CharField(max_length=100, blank=True)
    entry_time = models.DateTimeField(null=True)
    exit_time = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        inst = "OPT" if self.instrument_type == self.InstrumentType.OPTION else "STK"
        return f"[{inst}] {self.symbol} {self.direction} P&L:{self.pnl}"
