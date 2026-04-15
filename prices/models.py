from decimal import Decimal

import pandas as pd
import ta
from django.db import models


INDICATOR_CONFIGS = {
    "SMA": {"periods": [20, 50, 200]},
    "RSI": {"periods": [14, 21]},
    "MACD": {"configs": [{"fast": 12, "slow": 26, "signal": 9}]},
}


class Price(models.Model):
    security = models.ForeignKey(
        "securities.Security",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    date = models.DateField(db_index=True)
    open = models.DecimalField(max_digits=12, decimal_places=4)
    high = models.DecimalField(max_digits=12, decimal_places=4)
    low = models.DecimalField(max_digits=12, decimal_places=4)
    close = models.DecimalField(max_digits=12, decimal_places=4)
    volume = models.BigIntegerField()
    vwap = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    trade_count = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("security", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.security.symbol} {self.date} C:{self.close}"


class IntradayBar(models.Model):
    security = models.ForeignKey(
        "securities.Security",
        on_delete=models.CASCADE,
        related_name="intraday_bars",
    )
    timestamp = models.DateTimeField(db_index=True)
    open = models.DecimalField(max_digits=12, decimal_places=4)
    high = models.DecimalField(max_digits=12, decimal_places=4)
    low = models.DecimalField(max_digits=12, decimal_places=4)
    close = models.DecimalField(max_digits=12, decimal_places=4)
    volume = models.BigIntegerField()
    vwap = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    trade_count = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("security", "timestamp")
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["security", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.security.symbol} {self.timestamp} C:{self.close}"


class Indicator(models.Model):
    security = models.ForeignKey(
        "securities.Security",
        on_delete=models.CASCADE,
        related_name="indicators",
    )
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=50, db_index=True)  # e.g. SMA_50, RSI_14, MACD_12_26_9
    value = models.DecimalField(max_digits=16, decimal_places=6, null=True)

    class Meta:
        unique_together = ("security", "date", "name")
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["security", "name", "date"]),
        ]

    def __str__(self):
        return f"{self.security.symbol} {self.date} {self.name}={self.value}"

    @staticmethod
    def compute_for_security(security, configs=None):
        """Compute all configured indicators for a security from its price history.

        Returns a list of Indicator instances (unsaved) ready for bulk operations.
        """
        configs = configs or INDICATOR_CONFIGS

        prices = (
            Price.objects.filter(security=security)
            .order_by("date")
            .values_list("date", "close", "high", "low", "volume")
        )
        if not prices:
            return []

        dates, close, high, low, volume = zip(*prices)
        df = pd.DataFrame(
            {"close": close, "high": high, "low": low, "volume": volume},
            index=dates,
            dtype=float,
        )

        results = []

        # SMA
        for period in configs.get("SMA", {}).get("periods", []):
            series = ta.trend.sma_indicator(df["close"], window=period)
            name = f"SMA_{period}"
            for dt, val in zip(dates, series):
                results.append(Indicator(
                    security=security,
                    date=dt,
                    name=name,
                    value=Decimal(str(round(val, 6))) if pd.notna(val) else None,
                ))

        # RSI
        for period in configs.get("RSI", {}).get("periods", []):
            series = ta.momentum.rsi(df["close"], window=period)
            name = f"RSI_{period}"
            for dt, val in zip(dates, series):
                results.append(Indicator(
                    security=security,
                    date=dt,
                    name=name,
                    value=Decimal(str(round(val, 6))) if pd.notna(val) else None,
                ))

        # MACD
        for cfg in configs.get("MACD", {}).get("configs", []):
            fast, slow, sig = cfg["fast"], cfg["slow"], cfg["signal"]
            macd_line = ta.trend.macd(df["close"], window_slow=slow, window_fast=fast)
            macd_signal = ta.trend.macd_signal(
                df["close"], window_slow=slow, window_fast=fast, window_sign=sig
            )
            macd_hist = ta.trend.macd_diff(
                df["close"], window_slow=slow, window_fast=fast, window_sign=sig
            )
            prefix = f"MACD_{fast}_{slow}_{sig}"
            for dt, line, signal, hist in zip(dates, macd_line, macd_signal, macd_hist):
                for suffix, val in [("line", line), ("signal", signal), ("hist", hist)]:
                    results.append(Indicator(
                        security=security,
                        date=dt,
                        name=f"{prefix}_{suffix}",
                        value=Decimal(str(round(val, 6))) if pd.notna(val) else None,
                    ))

        return results
