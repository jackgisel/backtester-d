import numpy as np
import pytz
import pandas as pd

from .base import BaseStrategy, ParameterSpec, SetupScore, Signal

EASTERN = pytz.timezone("America/New_York")


class MomentumContinuation(BaseStrategy):
    """Momentum Continuation / First Pullback strategy.

    After a strong opening thrust (1%+ move in first 30 min), waits for
    the first pullback, then enters in the original direction when the
    pullback stalls. Captures the second leg of trending opens that
    ORB filters out via gap/range width limits.
    """

    name = "MOMENTUM"
    display_name = "Momentum Continuation"

    @classmethod
    def parameter_specs(cls) -> list[ParameterSpec]:
        return [
            ParameterSpec(
                "min_thrust_pct", "float", 1.0,
                min_value=0.5, max_value=2.5, step=0.25,
                description="Required initial directional move in first 30 min",
            ),
            ParameterSpec(
                "min_pullback_pct", "float", 0.3,
                min_value=0.1, max_value=0.8, step=0.1,
                description="Minimum retracement to qualify as pullback",
            ),
            ParameterSpec(
                "max_pullback_pct", "float", 0.8,
                min_value=0.4, max_value=1.5, step=0.1,
                description="Max retracement before trend is considered broken",
            ),
            ParameterSpec(
                "stop_loss_pct", "float", 0.5,
                min_value=0.2, max_value=1.0, step=0.1,
                description="Stop below pullback low for longs",
            ),
            ParameterSpec(
                "take_profit_pct", "float", 1.5,
                min_value=0.5, max_value=3.0, step=0.25,
                description="Take profit as % of entry",
            ),
            ParameterSpec(
                "entry_window_end_minutes", "int", 150,
                min_value=60, max_value=240, step=30,
                description="Pullback must occur within this window from open",
            ),
        ]

    def score_setup(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> SetupScore | None:
        if bars.empty or len(bars) < 30:
            return None

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        session_start = bars_et.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
        first_30 = bars_et[
            (bars_et.index >= session_start) &
            (bars_et.index < session_start + pd.Timedelta(minutes=30))
        ]
        if first_30.empty:
            return None

        open_price = float(first_30.iloc[0]["open"])
        high_30 = float(first_30["high"].max())
        low_30 = float(first_30["low"].min())

        up_move = (high_30 - open_price) / open_price * 100
        down_move = (open_price - low_30) / open_price * 100

        thrust = max(up_move, down_move)
        if thrust < self.min_thrust_pct:
            return None

        direction = "long" if up_move > down_move else "short"

        # Score based on thrust strength and volume
        thrust_score = 50 * min(1, thrust / 2.0)
        vol = float(first_30["volume"].astype(float).sum())
        vol_score = 30 * min(1, vol / 500000)

        # Bonus for prior day alignment
        trend_bonus = 0
        if prior_day_bars is not None and not prior_day_bars.empty:
            pdc = float(prior_day_bars.iloc[-1]["close"])
            pdo = float(prior_day_bars.iloc[0]["open"])
            if (direction == "long" and pdc > pdo) or (direction == "short" and pdc < pdo):
                trend_bonus = 20

        total = thrust_score + vol_score + trend_bonus

        return SetupScore(
            symbol=bars_et.attrs.get("symbol", ""),
            score=round(total, 2),
            direction=direction,
            metadata={"thrust_pct": round(thrust, 2), "direction": direction},
        )

    def generate_signals(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> list[Signal]:
        if bars.empty or len(bars) < 30:
            return []

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)

        session_start = bars_et.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
        window_end = session_start + pd.Timedelta(minutes=self.entry_window_end_minutes)
        session_close = session_start.replace(hour=13, minute=30)  # hard exit at 1:30pm

        # Compute initial thrust (first 30 min)
        first_30 = bars_et[
            (bars_et.index >= session_start) &
            (bars_et.index < session_start + pd.Timedelta(minutes=30))
        ]
        if first_30.empty:
            return []

        open_price = float(first_30.iloc[0]["open"])
        high_30 = float(first_30["high"].max())
        low_30 = float(first_30["low"].min())

        up_move = (high_30 - open_price) / open_price * 100
        down_move = (open_price - low_30) / open_price * 100

        if max(up_move, down_move) < self.min_thrust_pct:
            return []

        if up_move > down_move:
            thrust_dir = "long"
            thrust_extreme = high_30
        else:
            thrust_dir = "short"
            thrust_extreme = low_30

        # Look for pullback after the thrust
        signals = []
        in_trade = False
        trade_direction = None
        pullback_low = float("inf") if thrust_dir == "long" else 0
        pullback_started = False

        post_thrust = bars_et[bars_et.index >= session_start + pd.Timedelta(minutes=30)]

        for ts, bar in post_thrust.iterrows():
            close = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])

            if ts >= session_close:
                if in_trade:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action=f"exit_{trade_direction}",
                        price=close,
                        metadata={"reason": "eod"},
                    ))
                break

            if in_trade:
                continue

            if ts >= window_end:
                break

            if thrust_dir == "long":
                # Track pullback
                retracement = (thrust_extreme - low) / thrust_extreme * 100
                if retracement >= self.min_pullback_pct:
                    pullback_started = True
                    pullback_low = min(pullback_low, low)

                if retracement > self.max_pullback_pct:
                    break  # Trend broken

                # Stall: pullback started and bar's low is above prior pullback low
                if pullback_started and low > pullback_low and close > float(bar["open"]):
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action="enter_long",
                        price=close,
                        metadata={"thrust_high": thrust_extreme, "pullback_low": pullback_low},
                    ))
                    in_trade = True
                    trade_direction = "long"

            else:  # short
                retracement = (high - thrust_extreme) / thrust_extreme * 100
                if retracement >= self.min_pullback_pct:
                    pullback_started = True
                    pullback_low = max(pullback_low, high)  # pullback high for shorts

                if retracement > self.max_pullback_pct:
                    break

                if pullback_started and high < pullback_low and close < float(bar["open"]):
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action="enter_short",
                        price=close,
                        metadata={"thrust_low": thrust_extreme, "pullback_high": pullback_low},
                    ))
                    in_trade = True
                    trade_direction = "short"

        return signals
