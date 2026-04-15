import numpy as np
import pytz
import pandas as pd

from .base import BaseStrategy, ParameterSpec, SetupScore, Signal

EASTERN = pytz.timezone("America/New_York")


class VWAPReclaim(BaseStrategy):
    """VWAP Reclaim mean-reversion strategy.

    Waits for price to dip below VWAP, then enters when price reclaims
    VWAP with volume confirmation. Captures mean-reversion on days
    ORB sits out (rangebound, moderate volatility).
    """

    name = "VWAP_RECLAIM"
    display_name = "VWAP Reclaim"

    @classmethod
    def parameter_specs(cls) -> list[ParameterSpec]:
        return [
            ParameterSpec(
                "bars_below_vwap", "int", 3,
                min_value=2, max_value=6,
                description="Consecutive bars below VWAP before reclaim is valid",
            ),
            ParameterSpec(
                "volume_confirm_mult", "float", 1.2,
                min_value=1.0, max_value=2.5, step=0.1,
                description="Reclaim bar volume >= N * rolling 10-bar avg",
            ),
            ParameterSpec(
                "stop_loss_pct", "float", 0.7,
                min_value=0.2, max_value=1.5, step=0.1,
                description="Stop loss as % of entry",
            ),
            ParameterSpec(
                "take_profit_pct", "float", 1.2,
                min_value=0.5, max_value=3.0, step=0.1,
                description="Take profit as % of entry",
            ),
            ParameterSpec(
                "earliest_entry_minutes", "int", 30,
                min_value=15, max_value=90, step=15,
                description="Minutes after open before entries allowed",
            ),
            ParameterSpec(
                "entry_cutoff_minutes", "int", 300,
                min_value=120, max_value=360, step=30,
                description="No entries after this many minutes from open",
            ),
        ]

    def _compute_vwap(self, bars: pd.DataFrame) -> pd.Series:
        """Compute cumulative VWAP from bar data."""
        if "vwap" in bars.columns and bars["vwap"].notna().any():
            return bars["vwap"].astype(float)
        typical = (bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)) / 3
        cum_tp_vol = (typical * bars["volume"].astype(float)).cumsum()
        cum_vol = bars["volume"].astype(float).cumsum()
        return cum_tp_vol / cum_vol

    def score_setup(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> SetupScore | None:
        if bars.empty or len(bars) < 30:
            return None

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        vwap = self._compute_vwap(bars_et)
        close = bars_et["close"].astype(float)

        # Score: how much price has oscillated around VWAP (good for mean reversion)
        deviations = (close - vwap).abs() / vwap * 100
        avg_dev = float(deviations.mean())

        # Sweet spot: moderate deviation (0.3-1.5%) = good mean reversion potential
        if avg_dev < 0.1 or avg_dev > 2.0:
            return None

        # Check that VWAP data exists and volume is reasonable
        vol = bars_et["volume"].astype(float)
        if vol.sum() < 1000:
            return None

        # Score components
        dev_score = 40 * max(0, min(1, avg_dev / 1.0))  # higher deviation = more opportunity
        vol_score = 30 * max(0, min(1, float(vol.mean()) / 50000))  # liquid names score higher
        # Symmetry: roughly equal time above and below VWAP
        above = (close > vwap).sum()
        below = (close < vwap).sum()
        total = above + below
        balance = min(above, below) / max(total, 1)
        balance_score = 30 * balance  # 0.5 = perfect balance = 15 pts

        total_score = dev_score + vol_score + balance_score
        return SetupScore(
            symbol=bars_et.attrs.get("symbol", ""),
            score=round(total_score, 2),
            direction="neutral",
            metadata={"avg_dev": round(avg_dev, 3), "balance": round(balance, 2)},
        )

    def generate_signals(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> list[Signal]:
        if bars.empty or len(bars) < 30:
            return []

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        session_start = bars_et.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
        earliest = session_start + pd.Timedelta(minutes=self.earliest_entry_minutes)
        cutoff = session_start + pd.Timedelta(minutes=self.entry_cutoff_minutes)
        session_close = session_start.replace(hour=15, minute=45)

        vwap = self._compute_vwap(bars_et)
        close = bars_et["close"].astype(float)
        vol = bars_et["volume"].astype(float)
        vol_ma = vol.rolling(10, min_periods=1).mean()

        signals = []
        in_trade = False
        trade_direction = None
        bars_below = 0
        bars_above = 0

        for i, (ts, bar) in enumerate(bars_et.iterrows()):
            c = float(bar["close"])
            v = float(bar["volume"])
            vw = float(vwap.iloc[i]) if i < len(vwap) else c
            avg_v = float(vol_ma.iloc[i]) if i < len(vol_ma) else v

            if ts >= session_close:
                if in_trade:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action=f"exit_{trade_direction}",
                        price=c,
                        metadata={"reason": "eod"},
                    ))
                break

            # Track consecutive bars below/above VWAP
            if c < vw:
                bars_below += 1
                bars_above = 0
            elif c > vw:
                bars_above += 1
                bars_below = 0
            else:
                bars_below = 0
                bars_above = 0

            if in_trade or ts < earliest or ts >= cutoff:
                continue

            vol_ok = v >= avg_v * self.volume_confirm_mult

            # Long: was below VWAP, now reclaiming above
            if bars_above == 1 and bars_below == 0 and vol_ok:
                # Check if we had enough bars below before this reclaim
                lookback = close.iloc[max(0, i - self.bars_below_vwap - 1):i]
                vwap_lookback = vwap.iloc[max(0, i - self.bars_below_vwap - 1):i]
                if len(lookback) >= self.bars_below_vwap:
                    below_count = (lookback < vwap_lookback).sum()
                    if below_count >= self.bars_below_vwap:
                        signals.append(Signal(
                            timestamp=ts.tz_convert("UTC"),
                            action="enter_long",
                            price=c,
                            metadata={"vwap": vw},
                        ))
                        in_trade = True
                        trade_direction = "long"

            # Short: was above VWAP, now breaking below
            elif bars_below == 1 and bars_above == 0 and vol_ok:
                lookback = close.iloc[max(0, i - self.bars_above_vwap - 1):i] if hasattr(self, 'bars_above_vwap') else close.iloc[max(0, i - self.bars_below_vwap - 1):i]
                vwap_lookback = vwap.iloc[max(0, i - self.bars_below_vwap - 1):i]
                if len(lookback) >= self.bars_below_vwap:
                    above_count = (lookback > vwap_lookback).sum()
                    if above_count >= self.bars_below_vwap:
                        signals.append(Signal(
                            timestamp=ts.tz_convert("UTC"),
                            action="enter_short",
                            price=c,
                            metadata={"vwap": vw},
                        ))
                        in_trade = True
                        trade_direction = "short"

        return signals
