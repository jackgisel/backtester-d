import numpy as np
import pytz
import pandas as pd

from .base import BaseStrategy, ParameterSpec, SetupScore, Signal

EASTERN = pytz.timezone("America/New_York")


class PriorDayHLBreakout(BaseStrategy):
    """Prior Day High/Low Breakout strategy.

    Enters when price cleanly breaks above yesterday's high or below
    yesterday's low with volume confirmation. Targets afternoon trends
    that ORB misses when the morning is quiet.
    """

    name = "PDHL"
    display_name = "Prior Day H/L Breakout"

    @classmethod
    def parameter_specs(cls) -> list[ParameterSpec]:
        return [
            ParameterSpec(
                "volume_mult", "float", 1.5,
                min_value=1.0, max_value=3.0, step=0.5,
                description="Breakout bar volume >= N * trailing 20-bar avg",
            ),
            ParameterSpec(
                "entry_start_minutes", "int", 45,
                min_value=30, max_value=120, step=15,
                description="Minutes after open before watching PDH/PDL",
            ),
            ParameterSpec(
                "entry_cutoff_minutes", "int", 270,
                min_value=120, max_value=360, step=30,
                description="No entries after this many minutes from open",
            ),
            ParameterSpec(
                "stop_loss_pct", "float", 0.5,
                min_value=0.2, max_value=1.5, step=0.1,
                description="Stop below PDH for longs, above PDL for shorts",
            ),
            ParameterSpec(
                "take_profit_pct", "float", 1.5,
                min_value=0.5, max_value=4.0, step=0.5,
                description="Take profit as % of entry",
            ),
            ParameterSpec(
                "use_trend_filter", "categorical", True,
                choices=[True, False],
                description="Only trade in direction of prior day close vs open",
            ),
        ]

    def _get_prior_day_levels(self, prior_day_bars):
        if prior_day_bars is None or prior_day_bars.empty:
            return None, None, None, None
        pdh = float(prior_day_bars["high"].max())
        pdl = float(prior_day_bars["low"].min())
        pdo = float(prior_day_bars.iloc[0]["open"])
        pdc = float(prior_day_bars.iloc[-1]["close"])
        return pdh, pdl, pdo, pdc

    def score_setup(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> SetupScore | None:
        if bars.empty:
            return None

        pdh, pdl, pdo, pdc = self._get_prior_day_levels(prior_day_bars)
        if pdh is None:
            return None

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        current_price = float(bars_et.iloc[-1]["close"]) if len(bars_et) > 15 else float(bars_et.iloc[0]["open"])
        prior_range = pdh - pdl

        # Score: how close is current price to PDH or PDL?
        dist_to_pdh = abs(current_price - pdh) / prior_range if prior_range > 0 else 10
        dist_to_pdl = abs(current_price - pdl) / prior_range if prior_range > 0 else 10
        min_dist = min(dist_to_pdh, dist_to_pdl)

        # Close to level = high score (0 = at level, 1+ = far away)
        proximity_score = 40 * max(0, min(1, 1 - min_dist))

        # Prior day range quality — moderate range is ideal
        mid = (pdh + pdl) / 2
        range_pct = (prior_range / mid * 100) if mid > 0 else 0
        range_score = 30 * max(0, min(1, range_pct / 2.0))

        # Volume
        vol = float(bars_et["volume"].astype(float).mean())
        vol_score = 30 * max(0, min(1, vol / 100000))

        total = proximity_score + range_score + vol_score

        direction = "long" if dist_to_pdh < dist_to_pdl else "short"

        return SetupScore(
            symbol=bars_et.attrs.get("symbol", ""),
            score=round(total, 2),
            direction=direction,
            range_high=pdh,
            range_low=pdl,
            metadata={"pdh": pdh, "pdl": pdl, "proximity": round(min_dist, 3)},
        )

    def generate_signals(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> list[Signal]:
        if bars.empty:
            return []

        pdh, pdl, pdo, pdc = self._get_prior_day_levels(prior_day_bars)
        if pdh is None:
            return []

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        session_start = bars_et.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
        entry_start = session_start + pd.Timedelta(minutes=self.entry_start_minutes)
        cutoff = session_start + pd.Timedelta(minutes=self.entry_cutoff_minutes)
        session_close = session_start.replace(hour=15, minute=45)

        # Trend filter
        prior_bullish = pdc > pdo if pdc and pdo else True

        vol = bars_et["volume"].astype(float)
        vol_ma = vol.rolling(20, min_periods=1).mean()

        signals = []
        in_trade = False
        trade_direction = None

        for i, (ts, bar) in enumerate(bars_et.iterrows()):
            close = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            v = float(bar["volume"])
            avg_v = float(vol_ma.iloc[i]) if i < len(vol_ma) else v

            if ts >= session_close:
                if in_trade:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action=f"exit_{trade_direction}",
                        price=close,
                        metadata={"reason": "eod"},
                    ))
                break

            if in_trade or ts < entry_start or ts >= cutoff:
                continue

            vol_ok = v >= avg_v * self.volume_mult

            # Long: close above PDH with volume
            can_long = not self.use_trend_filter or prior_bullish
            if close > pdh and vol_ok and can_long:
                signals.append(Signal(
                    timestamp=ts.tz_convert("UTC"),
                    action="enter_long",
                    price=pdh,
                    metadata={"pdh": pdh, "pdl": pdl},
                ))
                in_trade = True
                trade_direction = "long"

            # Short: close below PDL with volume
            can_short = not self.use_trend_filter or not prior_bullish
            if not in_trade and close < pdl and vol_ok and can_short:
                signals.append(Signal(
                    timestamp=ts.tz_convert("UTC"),
                    action="enter_short",
                    price=pdl,
                    metadata={"pdh": pdh, "pdl": pdl},
                ))
                in_trade = True
                trade_direction = "short"

        return signals
