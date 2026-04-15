"""Live ORB trading state machine with hybrid (stock + options) support.

States: AWAITING_OPEN -> BUILDING_RANGE -> SCORING -> WATCHING -> IN_TRADE -> EOD_FLATTEN -> DONE
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum

import pytz

logger = logging.getLogger("trading.live")
EASTERN = pytz.timezone("America/New_York")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
FLATTEN_TIME = time(15, 55)


class State(str, Enum):
    AWAITING_OPEN = "AWAITING_OPEN"
    BUILDING_RANGE = "BUILDING_RANGE"
    SCORING = "SCORING"
    WATCHING = "WATCHING"
    IN_TRADE = "IN_TRADE"
    EOD_FLATTEN = "EOD_FLATTEN"
    DONE = "DONE"


@dataclass
class BarData:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class LiveORBState:
    """Per-symbol state machine for live ORB trading."""

    symbol: str
    opening_range_minutes: int = 15
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 2.0
    use_atr_stops: bool = False
    atr_stop_mult: float = 1.5
    atr_tp_mult: float = 4.0
    volume_threshold: float = 1.0
    entry_cutoff_minutes: int = 180
    use_trend_filter: bool = True
    capital: float = 10_000.0
    position_size_pct: float = 0.50

    # Internal state
    state: State = State.AWAITING_OPEN
    range_bars: list = field(default_factory=list)
    range_high: float = 0.0
    range_low: float = float("inf")
    avg_range_volume: float = 0.0
    setup_score: float = 0.0
    position_direction: str | None = None
    entry_price: float | None = None
    shares: float = 0.0
    stop_price: float = 0.0
    take_profit_price: float = 0.0
    daily_pnl: float = 0.0
    approved_to_trade: bool = False  # set by coordinator after scoring

    def on_bar(self, bar: BarData) -> dict | None:
        """Process a 1-minute bar. Returns an action dict or None."""
        et_time = bar.timestamp.astimezone(EASTERN).time()

        if self.state == State.DONE:
            return None

        if self.state == State.AWAITING_OPEN:
            if et_time >= MARKET_OPEN:
                self.state = State.BUILDING_RANGE
                logger.info(f"[{self.symbol}] BUILDING_RANGE started")
            return None

        if self.state == State.BUILDING_RANGE:
            self.range_bars.append(bar)
            self.range_high = max(self.range_high, bar.high)
            self.range_low = min(self.range_low, bar.low)

            if len(self.range_bars) >= self.opening_range_minutes:
                self.avg_range_volume = (
                    sum(b.volume for b in self.range_bars) / len(self.range_bars)
                )
                self._compute_score()
                self.state = State.SCORING  # wait for coordinator
                logger.info(
                    f"[{self.symbol}] SCORING - range={self.range_high:.2f}/{self.range_low:.2f} "
                    f"score={self.setup_score:.1f}"
                )
                return {
                    "action": "scored",
                    "score": self.setup_score,
                    "range_high": self.range_high,
                    "range_low": self.range_low,
                }
            return None

        # Check flatten time
        if et_time >= FLATTEN_TIME:
            if self.state == State.IN_TRADE:
                self.state = State.EOD_FLATTEN
                return {"action": "exit", "reason": "eod_flatten"}
            self.state = State.DONE
            return None

        if self.state == State.SCORING:
            # Waiting for coordinator to approve
            if self.approved_to_trade:
                self.state = State.WATCHING
            return None

        if self.state == State.WATCHING:
            return self._check_breakout(bar, et_time)

        if self.state == State.IN_TRADE:
            return None  # Alpaca bracket order handles exits

        return None

    def approve(self):
        """Called by coordinator to approve this symbol for trading."""
        self.approved_to_trade = True
        self.state = State.WATCHING
        logger.info(f"[{self.symbol}] APPROVED for trading (score={self.setup_score:.1f})")

    def reject(self):
        """Called by coordinator to reject this symbol."""
        self.state = State.DONE
        logger.info(f"[{self.symbol}] REJECTED (score={self.setup_score:.1f})")

    def _compute_score(self):
        """Compute setup quality score from opening range data."""
        range_width = self.range_high - self.range_low
        range_mid = (self.range_high + self.range_low) / 2
        if range_mid == 0:
            self.setup_score = 0
            return

        range_pct = range_width / range_mid * 100

        # Tightness (30 pts) — tighter is better
        tightness = 30 * max(0, min(1, 1 - (range_pct / 2.0)))

        # Volume (25 pts)
        vol_score = 25 * min(1, self.avg_range_volume / 100000)

        # Range symmetry (15 pts) — balanced volume distribution
        if len(self.range_bars) > 1:
            first_half_vol = sum(b.volume for b in self.range_bars[:len(self.range_bars)//2])
            second_half_vol = sum(b.volume for b in self.range_bars[len(self.range_bars)//2:])
            total_vol = first_half_vol + second_half_vol
            balance = min(first_half_vol, second_half_vol) / max(total_vol, 1)
            sym_score = 15 * balance * 2
        else:
            sym_score = 7.5

        # Base score (gap quality needs prior day data we don't have in state machine)
        gap_score = 10  # neutral default

        self.setup_score = tightness + vol_score + sym_score + gap_score

    def _check_breakout(self, bar: BarData, et_time) -> dict | None:
        minutes_from_open = (et_time.hour * 60 + et_time.minute) - (9 * 60 + 30)
        if minutes_from_open > self.entry_cutoff_minutes:
            self.state = State.DONE
            return None

        volume_ok = (
            self.volume_threshold == 0
            or self.avg_range_volume == 0
            or bar.volume >= self.avg_range_volume * self.volume_threshold
        )

        if bar.high > self.range_high and volume_ok:
            return self._enter("long", self.range_high)

        if bar.low < self.range_low and volume_ok:
            return self._enter("short", self.range_low)

        return None

    def _enter(self, direction: str, price: float) -> dict:
        allocated = self.capital * self.position_size_pct
        self.shares = allocated / price
        self.entry_price = price
        self.position_direction = direction

        sl_pct = self.stop_loss_pct / 100
        tp_pct = self.take_profit_pct / 100

        if direction == "long":
            self.stop_price = price * (1 - sl_pct)
            self.take_profit_price = price * (1 + tp_pct)
        else:
            self.stop_price = price * (1 + sl_pct)
            self.take_profit_price = price * (1 - tp_pct)

        self.state = State.IN_TRADE
        logger.info(
            f"[{self.symbol}] ENTER {direction} @ {price:.2f} "
            f"shares={self.shares:.2f} stop={self.stop_price:.2f} tp={self.take_profit_price:.2f}"
        )

        return {
            "action": f"enter_{direction}",
            "price": price,
            "shares": round(self.shares, 2),
            "stop": round(self.stop_price, 2),
            "tp": round(self.take_profit_price, 2),
            "score": self.setup_score,
        }
