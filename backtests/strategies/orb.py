import numpy as np
import pytz
import pandas as pd

from .base import BaseStrategy, ParameterSpec, SetupScore, Signal

EASTERN = pytz.timezone("America/New_York")
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16


class OpeningRangeBreakout(BaseStrategy):
    """Opening Range Breakout (ORB) with setup quality scoring.

    Scan many symbols, score each setup, trade only the best 1-2.
    """

    name = "ORB"
    display_name = "Opening Range Breakout"

    @classmethod
    def parameter_specs(cls) -> list[ParameterSpec]:
        return [
            ParameterSpec(
                "opening_range_minutes", "int", 15,
                min_value=1, max_value=60,
                description="Minutes after open to define the opening range",
            ),
            ParameterSpec(
                "stop_loss_pct", "float", 1.0,
                min_value=0.1, max_value=5.0, step=0.1,
                description="Stop loss % (fallback when ATR stops disabled)",
            ),
            ParameterSpec(
                "take_profit_pct", "float", 2.0,
                min_value=0.1, max_value=10.0, step=0.1,
                description="Take profit % (fallback when ATR stops disabled)",
            ),
            ParameterSpec(
                "use_atr_stops", "categorical", True,
                choices=[True, False],
                description="Use ATR-based dynamic stops",
            ),
            ParameterSpec(
                "atr_stop_mult", "float", 1.5,
                min_value=0.5, max_value=4.0, step=0.5,
                description="Stop distance as ATR multiple",
            ),
            ParameterSpec(
                "atr_tp_mult", "float", 4.0,
                min_value=1.0, max_value=8.0, step=0.5,
                description="Take profit as ATR multiple",
            ),
            ParameterSpec(
                "entry_cutoff_minutes", "int", 180,
                min_value=30, max_value=300,
                description="No entries after this many minutes from open",
            ),
            ParameterSpec(
                "volume_threshold", "float", 1.0,
                min_value=0.0, max_value=5.0, step=0.1,
                description="Breakout bar volume >= N * avg range volume",
            ),
            ParameterSpec(
                "max_gap_pct", "float", 3.0,
                min_value=0.5, max_value=10.0, step=0.5,
                description="Skip day if gap exceeds N%",
            ),
            ParameterSpec(
                "min_range_pct", "float", 0.3,
                min_value=0.0, max_value=2.0, step=0.1,
                description="Skip if range < N% of price",
            ),
            ParameterSpec(
                "max_range_pct", "float", 1.75,
                min_value=0.5, max_value=10.0, step=0.25,
                description="Skip if range > N% of price",
            ),
            ParameterSpec(
                "use_trend_filter", "categorical", True,
                choices=[True, False],
                description="Only trade in direction of prior day's trend",
            ),
        ]

    def _get_session_bounds(self, bars_et):
        first_bar = bars_et.index[0]
        session_start = first_bar.replace(
            hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0,
        )
        range_end = session_start + pd.Timedelta(minutes=self.opening_range_minutes)
        cutoff_time = session_start + pd.Timedelta(minutes=self.entry_cutoff_minutes)
        session_close = session_start.replace(hour=MARKET_CLOSE_HOUR, minute=0)
        return session_start, range_end, cutoff_time, session_close

    def _compute_range(self, bars_et, session_start, range_end):
        range_bars = bars_et[
            (bars_et.index >= session_start) & (bars_et.index < range_end)
        ]
        if range_bars.empty:
            return None, None, None, None, None, None

        range_high = float(range_bars["high"].max())
        range_low = float(range_bars["low"].min())
        range_mid = (range_high + range_low) / 2
        range_width = range_high - range_low
        avg_range_volume = float(range_bars["volume"].mean())
        return range_bars, range_high, range_low, range_mid, range_width, avg_range_volume

    def _compute_atr(self, range_bars, range_width, range_mid):
        """Compute ATR from opening range bars."""
        if not self.use_atr_stops or len(range_bars) < 2:
            return None
        highs = range_bars["high"].astype(float)
        lows = range_bars["low"].astype(float)
        closes = range_bars["close"].astype(float)
        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - closes.shift(1)),
                np.abs(lows - closes.shift(1)),
            ),
        ).dropna()
        return float(tr.mean()) if len(tr) > 0 else range_width

    def _get_prior_day_trend(self, prior_day_bars):
        """Returns 1 for bullish, -1 for bearish, 0 for neutral."""
        if prior_day_bars is None or prior_day_bars.empty:
            return 0
        prior_open = float(prior_day_bars.iloc[0]["open"])
        prior_close = float(prior_day_bars.iloc[-1]["close"])
        if prior_close > prior_open:
            return 1
        elif prior_close < prior_open:
            return -1
        return 0

    def score_setup(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> SetupScore | None:
        """Score ORB setup quality (0-100). Returns None if filtered out."""
        if bars.empty:
            return None

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)
        session_start, range_end, _, _ = self._get_session_bounds(bars_et)

        result = self._compute_range(bars_et, session_start, range_end)
        range_bars, range_high, range_low, range_mid, range_width, avg_range_volume = result
        if range_bars is None or range_mid == 0:
            return None

        range_pct = (range_width / range_mid) * 100

        # Hard filters — skip day entirely
        if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
            return None

        # Gap filter
        pre_range = bars_et[bars_et.index < session_start]
        gap_pct = 0.0
        prev_close = None
        if not pre_range.empty:
            prev_close = float(pre_range.iloc[-1]["close"])
            open_price = float(range_bars.iloc[0]["open"])
            gap_pct = abs(open_price - prev_close) / prev_close * 100
            if gap_pct > self.max_gap_pct:
                return None

        # --- SCORING ---
        # 1. Range Tightness (30 pts) — today's range vs prior day's full range
        #    Use prior day's high-low as the ATR proxy (not opening range ATR)
        if prior_day_bars is not None and not prior_day_bars.empty:
            prior_range = float(prior_day_bars["high"].max()) - float(prior_day_bars["low"].min())
            prior_mid = (float(prior_day_bars["high"].max()) + float(prior_day_bars["low"].min())) / 2
            prior_range_pct = (prior_range / prior_mid * 100) if prior_mid > 0 else 2.0
        else:
            prior_range_pct = 2.0  # sensible default

        # Ratio < 0.5 means today's opening range is less than half of yesterday's full range (coiled)
        ratio = range_pct / prior_range_pct if prior_range_pct > 0 else 1.0
        tightness_score = 30 * max(0, min(1, 1 - (ratio * 0.7)))

        # 2. Relative Volume (25 pts)
        #    Compare opening range volume to prior day's opening range volume
        total_range_vol = float(range_bars["volume"].sum())
        if prior_day_bars is not None and not prior_day_bars.empty:
            prior_et = prior_day_bars.copy()
            if prior_et.index.tz is None:
                prior_et.index = prior_et.index.tz_localize("UTC")
            prior_et.index = prior_et.index.tz_convert(EASTERN)
            prior_start = prior_et.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
            prior_range_end = prior_start + pd.Timedelta(minutes=self.opening_range_minutes)
            prior_range_bars = prior_et[(prior_et.index >= prior_start) & (prior_et.index < prior_range_end)]
            prior_range_vol = float(prior_range_bars["volume"].sum()) if not prior_range_bars.empty else total_range_vol
        else:
            prior_range_vol = total_range_vol

        rvol = total_range_vol / prior_range_vol if prior_range_vol > 0 else 1.0
        rvol_score = 25 * max(0, min(1, (rvol - 0.5) / (3.0 - 0.5)))

        # 3. Gap Quality (20 pts)
        abs_gap = abs(gap_pct)
        if abs_gap < 0.2:
            gap_base = 5
        elif abs_gap <= 2.0:
            gap_base = 20
        elif abs_gap <= 3.0:
            gap_base = 10
        else:
            gap_base = 0
        gap_score = min(gap_base, 20)

        # 4. Range Position vs prior day (15 pts)
        position_score = 7.5  # default neutral
        if prior_day_bars is not None and not prior_day_bars.empty:
            prior_high = float(prior_day_bars["high"].max())
            prior_low = float(prior_day_bars["low"].min())
            clearance = min(
                abs(range_mid - prior_high), abs(range_mid - prior_low)
            )
            clearance_ratio = clearance / range_width if range_width > 0 else 0
            position_score = 15 * max(0, min(1, clearance_ratio / 3.0))

        # 5. Range Symmetry (10 pts) — VWAP near midpoint
        if "vwap" in range_bars.columns:
            range_vwap = float(range_bars["vwap"].dropna().mean()) if range_bars["vwap"].notna().any() else range_mid
        else:
            range_vwap = range_mid
        midpoint = (range_high + range_low) / 2
        half_width = range_width / 2 if range_width > 0 else 1
        symmetry = 1 - abs(range_vwap - midpoint) / half_width
        symmetry_score = 10 * max(0, min(1, symmetry))

        total_score = tightness_score + rvol_score + gap_score + position_score + symmetry_score

        # Determine direction bias
        prior_trend = self._get_prior_day_trend(prior_day_bars)
        if gap_pct > 0.2:
            direction = "long"
        elif gap_pct < -0.2:
            direction = "short"
        else:
            direction = "long" if prior_trend >= 0 else "short"

        return SetupScore(
            symbol=bars_et.attrs.get("symbol", ""),
            score=round(total_score, 2),
            direction=direction,
            range_high=range_high,
            range_low=range_low,
            range_width_pct=round(range_pct, 4),
            volume_ratio=round(rvol, 2),
            metadata={
                "tightness": round(tightness_score, 1),
                "rvol": round(rvol_score, 1),
                "gap": round(gap_score, 1),
                "position": round(position_score, 1),
                "symmetry": round(symmetry_score, 1),
                "gap_pct": round(gap_pct, 2),
                "prior_range_pct": round(prior_range_pct, 4),
            },
        )

    def generate_signals(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> list[Signal]:
        if bars.empty:
            return []

        bars_et = bars.copy()
        bars_et.index = bars_et.index.tz_convert(EASTERN)
        session_start, range_end, cutoff_time, session_close = self._get_session_bounds(bars_et)

        result = self._compute_range(bars_et, session_start, range_end)
        range_bars, range_high, range_low, range_mid, range_width, avg_range_volume = result
        if range_bars is None:
            return []

        range_pct = (range_width / range_mid) * 100 if range_mid > 0 else 0

        # Hard filters
        if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
            return []

        # Gap filter
        pre_range = bars_et[bars_et.index < session_start]
        if not pre_range.empty:
            prev_close = float(pre_range.iloc[-1]["close"])
            open_price = float(range_bars.iloc[0]["open"])
            gap_pct = abs(open_price - prev_close) / prev_close * 100
            if gap_pct > self.max_gap_pct:
                return []

        # Prior-day trend filter
        prior_trend = self._get_prior_day_trend(prior_day_bars)

        # ATR-based stops
        atr = self._compute_atr(range_bars, range_width, range_mid)
        if atr and self.use_atr_stops and range_mid > 0:
            self.stop_loss_pct = (atr * self.atr_stop_mult) / range_mid * 100
            self.take_profit_pct = (atr * self.atr_tp_mult) / range_mid * 100

        signals = []
        in_trade = False
        trade_direction = None
        post_range = bars_et[bars_et.index >= range_end]

        for ts, bar in post_range.iterrows():
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            vol = float(bar["volume"])

            # EOD exit
            if ts >= session_close:
                if in_trade:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action=f"exit_{trade_direction}",
                        price=close,
                        metadata={"reason": "eod"},
                    ))
                break

            if not in_trade and ts < cutoff_time:
                volume_ok = (
                    self.volume_threshold == 0
                    or avg_range_volume == 0
                    or vol >= avg_range_volume * self.volume_threshold
                )

                # Long: close confirms above range high + trend filter
                can_long = not self.use_trend_filter or prior_trend >= 0
                can_short = not self.use_trend_filter or prior_trend <= 0

                if high > range_high and volume_ok and can_long:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action="enter_long",
                        price=range_high,
                        metadata={"range_high": range_high, "range_low": range_low},
                    ))
                    in_trade = True
                    trade_direction = "long"

                elif low < range_low and volume_ok and can_short:
                    signals.append(Signal(
                        timestamp=ts.tz_convert("UTC"),
                        action="enter_short",
                        price=range_low,
                        metadata={"range_high": range_high, "range_low": range_low},
                    ))
                    in_trade = True
                    trade_direction = "short"

        return signals
