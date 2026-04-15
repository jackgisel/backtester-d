from dataclasses import dataclass

import pandas as pd
import pytz

from prices.models import IntradayBar
from .metrics import calculate_metrics
from .strategies.base import BaseStrategy, SetupScore, Signal

EASTERN = pytz.timezone("America/New_York")


@dataclass
class Position:
    direction: str  # "long" | "short"
    entry_timestamp: pd.Timestamp
    entry_price: float
    shares: float
    stop_price: float
    take_profit_price: float


@dataclass
class BacktestResult:
    trades: list[dict]
    equity_curve: list[dict]  # [{t: epoch_ms, v: float}]
    metrics: dict


@dataclass
class PortfolioResult:
    per_symbol: dict[str, BacktestResult]
    combined_equity_curve: list[dict]
    combined_metrics: dict
    total_capital: float


class BacktestEngine:
    """Bar-level backtest simulation with realistic cost modeling.

    Improvements over naive backtester:
    - Slippage model (adverse fill adjustment)
    - Next-bar entry fills (no same-bar execution)
    - Configurable position sizing (default 25%)
    - Intraday equity tracking for accurate max drawdown
    - Daily loss limit and consecutive loss halt
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 10_000.0,
        commission_per_share: float = 0.005,
        position_size_pct: float = 0.25,
        slippage_pct: float = 0.05,
        max_daily_loss_pct: float = 2.0,
        max_consecutive_losses: int = 3,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_per_share = commission_per_share
        self.position_size_pct = position_size_pct
        self.slippage_pct = slippage_pct / 100  # convert to decimal
        self.max_daily_loss_pct = max_daily_loss_pct / 100
        self.max_consecutive_losses = max_consecutive_losses

    def run(self, security, start_date, end_date) -> BacktestResult:
        from django.conf import settings as django_settings
        blocked = getattr(django_settings, "BLOCKED_SYMBOLS", [])
        if security.symbol in blocked:
            raise ValueError(f"{security.symbol} is blocked from trading")

        bars_qs = (
            IntradayBar.objects.filter(
                security=security,
                timestamp__date__gte=start_date,
                timestamp__date__lte=end_date,
            )
            .order_by("timestamp")
            .values("timestamp", "open", "high", "low", "close", "volume", "vwap")
        )

        if not bars_qs.exists():
            raise ValueError(
                f"No intraday bars for {security.symbol} "
                f"between {start_date} and {end_date}"
            )

        df = pd.DataFrame.from_records(bars_qs)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        for col in ("open", "high", "low", "close", "vwap"):
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float)

        capital = self.initial_capital
        all_trades = []
        equity_points = []
        all_intraday_equity = []
        consecutive_losses = 0

        # Group by ET trading date
        df["et_date"] = df.index.tz_convert(EASTERN).date

        for trade_date, day_bars in df.groupby("et_date"):
            day_bars = day_bars.drop(columns=["et_date"])

            day_start_capital = capital
            signals = self.strategy.generate_signals(day_bars)

            day_trades, capital, intraday_eq = self._simulate_day(
                day_bars, signals, capital, consecutive_losses,
            )

            # Track consecutive losses across days
            for t in day_trades:
                if t["pnl"] < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0

            all_trades.extend(day_trades)
            all_intraday_equity.extend(intraday_eq)
            equity_points.append({
                "t": int(day_bars.index[-1].timestamp() * 1000),
                "v": round(capital, 2),
            })

        metrics = calculate_metrics(all_trades, self.initial_capital, equity_points)
        return BacktestResult(
            trades=all_trades, equity_curve=equity_points, metrics=metrics,
        )

    def _simulate_day(
        self,
        bars: pd.DataFrame,
        signals: list[Signal],
        capital: float,
        consecutive_losses: int,
    ) -> tuple[list[dict], float, list[float]]:
        signal_map = {s.timestamp: s for s in signals}
        position: Position | None = None
        trades = []
        pending_entry: Signal | None = None  # next-bar fill delay
        day_start_capital = capital
        day_halted = False
        intraday_equity = []

        for ts, bar in bars.iterrows():
            bar_open = float(bar["open"])
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

            # Execute pending entry at this bar's open (next-bar fill)
            if pending_entry is not None and position is None and not day_halted:
                position, capital = self._open(pending_entry, capital, bar_open)
                pending_entry = None

            # Check stop/TP on existing position
            if position is not None:
                hit, reason = self._check_exit(high, low, position)
                if hit:
                    exit_price = self._fill_exit(position, reason)
                    trade, capital = self._close(position, ts, exit_price, capital, reason)
                    trades.append(trade)
                    position = None

                    # Update consecutive losses and check halt
                    if trade["pnl"] < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                    if consecutive_losses >= self.max_consecutive_losses:
                        day_halted = True

            # Check for signal-based exit (e.g. EOD)
            signal = signal_map.get(ts)
            if signal and position is not None:
                if signal.action in ("exit_long", "exit_short"):
                    exit_price = self._apply_slippage(
                        signal.price, position.direction, is_exit=True,
                    )
                    trade, capital = self._close(
                        position, ts, exit_price, capital,
                        signal.metadata.get("reason", "signal"),
                    )
                    trades.append(trade)
                    position = None

            # Check daily loss limit
            daily_loss = (day_start_capital - capital) / day_start_capital
            if daily_loss >= self.max_daily_loss_pct:
                day_halted = True

            # Queue entry signal for next-bar fill (not same bar)
            if signal and position is None and pending_entry is None and not day_halted:
                if signal.action in ("enter_long", "enter_short"):
                    pending_entry = signal

            # Track intraday equity
            if position is not None:
                if position.direction == "long":
                    mark = capital + (close - position.entry_price) * position.shares
                else:
                    mark = capital + (position.entry_price - close) * position.shares
            else:
                mark = capital
            intraday_equity.append(mark)

        # Force-close any position still open at end of day
        if position is not None:
            last_ts = bars.index[-1]
            last_close = float(bars.iloc[-1]["close"])
            exit_price = self._apply_slippage(last_close, position.direction, is_exit=True)
            trade, capital = self._close(position, last_ts, exit_price, capital, "eod")
            trades.append(trade)

        return trades, capital, intraday_equity

    def _check_exit(
        self, high: float, low: float, pos: Position,
    ) -> tuple[bool, str | None]:
        if pos.direction == "long":
            if low <= pos.stop_price:
                return True, "stop_loss"
            if high >= pos.take_profit_price:
                return True, "take_profit"
        else:
            if high >= pos.stop_price:
                return True, "stop_loss"
            if low <= pos.take_profit_price:
                return True, "take_profit"
        return False, None

    def _fill_exit(self, pos: Position, reason: str) -> float:
        """Fill stop/TP with slippage applied adversarially."""
        if reason == "stop_loss":
            base = pos.stop_price
            # Slippage makes stop fills worse
            if pos.direction == "long":
                return base * (1 - self.slippage_pct)
            else:
                return base * (1 + self.slippage_pct)
        if reason == "take_profit":
            return pos.take_profit_price
        return pos.entry_price

    def _apply_slippage(self, price: float, direction: str, is_exit: bool) -> float:
        """Apply slippage adversarially (worse fills for the trader)."""
        if is_exit:
            if direction == "long":
                return price * (1 - self.slippage_pct)  # sell lower
            else:
                return price * (1 + self.slippage_pct)  # buy-to-cover higher
        else:
            if direction == "long":
                return price * (1 + self.slippage_pct)  # buy higher
            else:
                return price * (1 - self.slippage_pct)  # sell higher (short entry)
        return price

    def _open(self, signal: Signal, capital: float, next_bar_open: float) -> tuple[Position, float]:
        """Open position at next bar's open with slippage."""
        direction = "long" if signal.action == "enter_long" else "short"
        entry_price = self._apply_slippage(next_bar_open, direction, is_exit=False)

        allocated = capital * self.position_size_pct
        shares = allocated / entry_price
        commission = shares * self.commission_per_share
        capital -= commission

        sl_pct = self.strategy.stop_loss_pct / 100
        tp_pct = self.strategy.take_profit_pct / 100

        if direction == "long":
            stop = entry_price * (1 - sl_pct)
            tp = entry_price * (1 + tp_pct)
        else:
            stop = entry_price * (1 + sl_pct)
            tp = entry_price * (1 - tp_pct)

        position = Position(
            direction=direction,
            entry_timestamp=signal.timestamp,
            entry_price=entry_price,
            shares=shares,
            stop_price=stop,
            take_profit_price=tp,
        )
        return position, capital

    def _close(
        self,
        pos: Position,
        ts: pd.Timestamp,
        exit_price: float,
        capital: float,
        reason: str,
    ) -> tuple[dict, float]:
        commission = pos.shares * self.commission_per_share
        if pos.direction == "long":
            gross_pnl = (exit_price - pos.entry_price) * pos.shares
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.shares
        net_pnl = gross_pnl - commission
        capital += net_pnl
        duration = int((ts - pos.entry_timestamp).total_seconds() / 60)

        trade = {
            "direction": pos.direction,
            "entry_timestamp": pos.entry_timestamp,
            "exit_timestamp": ts,
            "entry_price": round(pos.entry_price, 4),
            "exit_price": round(exit_price, 4),
            "shares": round(pos.shares, 6),
            "pnl": round(net_pnl, 4),
            "pnl_pct": round(
                (net_pnl / (pos.entry_price * pos.shares)) * 100, 4,
            ),
            "commission": round(commission, 4),
            "exit_reason": reason,
            "duration_minutes": duration,
        }
        return trade, capital

    def run_portfolio(
        self,
        securities: list,
        start_date,
        end_date,
        capital_per_symbol: float | None = None,
    ) -> PortfolioResult:
        """Run backtest across multiple symbols with equal capital allocation."""
        n = len(securities)
        alloc = capital_per_symbol or (self.initial_capital / n)
        original_capital = self.initial_capital

        results = {}
        for sec in securities:
            self.initial_capital = alloc
            try:
                results[sec.symbol] = self.run(sec, start_date, end_date)
            except ValueError:
                pass

        self.initial_capital = original_capital

        # Build combined equity curve by summing per-symbol curves
        all_timestamps = set()
        for r in results.values():
            for pt in r.equity_curve:
                all_timestamps.add(pt["t"])

        combined_curve = []
        for t in sorted(all_timestamps):
            total = 0.0
            for r in results.values():
                # Find the most recent equity point <= t
                latest = alloc
                for pt in r.equity_curve:
                    if pt["t"] <= t:
                        latest = pt["v"]
                total += latest
            combined_curve.append({"t": t, "v": round(total, 2)})

        # Aggregate all trades for combined metrics
        all_trades = []
        for r in results.values():
            all_trades.extend(r.trades)

        combined_metrics = calculate_metrics(
            all_trades, original_capital, combined_curve,
        )

        return PortfolioResult(
            per_symbol=results,
            combined_equity_curve=combined_curve,
            combined_metrics=combined_metrics,
            total_capital=original_capital,
        )

    def run_selective(
        self,
        securities: list,
        start_date,
        end_date,
        max_trades_per_day: int = 2,
        capital_per_trade_pct: float = 0.50,
        score_threshold: float = 60.0,
    ) -> PortfolioResult:
        """Scan many symbols, score setups, trade only the best 1-2 per day.

        Concentrates capital on the highest-scoring setups instead of
        spreading thin across all symbols.
        """
        from django.conf import settings as django_settings
        blocked = getattr(django_settings, "BLOCKED_SYMBOLS", [])

        # Load all bars upfront
        all_bars: dict[str, pd.DataFrame] = {}
        sec_map: dict[str, object] = {}
        for sec in securities:
            if sec.symbol in blocked:
                continue
            qs = (
                IntradayBar.objects.filter(
                    security=sec,
                    timestamp__date__gte=start_date,
                    timestamp__date__lte=end_date,
                )
                .order_by("timestamp")
                .values("timestamp", "open", "high", "low", "close", "volume", "vwap")
            )
            if not qs.exists():
                continue
            df = pd.DataFrame.from_records(qs)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
            for col in ("open", "high", "low", "close", "vwap"):
                df[col] = df[col].astype(float)
            df["volume"] = df["volume"].astype(float)
            df["et_date"] = df.index.tz_convert(EASTERN).date
            all_bars[sec.symbol] = df
            sec_map[sec.symbol] = sec

        # Collect all unique trading dates
        all_dates = sorted({
            d for df in all_bars.values() for d in df["et_date"].unique()
        })

        capital = self.initial_capital
        all_trades = []
        equity_points = []
        selection_log = []
        per_symbol_trades: dict[str, list] = {s: [] for s in all_bars}
        per_symbol_equity: dict[str, list] = {s: [] for s in all_bars}
        consecutive_losses = 0

        prev_day_bars: dict[str, pd.DataFrame] = {}

        for trade_date in all_dates:
            # 1. Score every symbol's setup
            scored: list[SetupScore] = []
            day_bar_cache: dict[str, pd.DataFrame] = {}

            for symbol, df in all_bars.items():
                day_df = df[df["et_date"] == trade_date].drop(columns=["et_date"])
                if day_df.empty:
                    continue
                day_bar_cache[symbol] = day_df
                day_df.attrs["symbol"] = symbol

                prior = prev_day_bars.get(symbol)
                score = self.strategy.score_setup(day_df, prior)
                if score is not None:
                    score.symbol = symbol
                    scored.append(score)

            # 2. Rank and select top N above threshold
            scored.sort(key=lambda s: s.score, reverse=True)
            selected = [s for s in scored if s.score >= score_threshold][:max_trades_per_day]

            selection_log.append({
                "date": str(trade_date),
                "candidates": len(scored),
                "selected": [{"symbol": s.symbol, "score": s.score, "dir": s.direction} for s in selected],
                "top_scores": [{"symbol": s.symbol, "score": s.score} for s in scored[:5]],
            })

            # 3. Run simulation only on selected symbols
            trade_capital = capital * capital_per_trade_pct

            for setup in selected:
                symbol = setup.symbol
                day_df = day_bar_cache[symbol]
                prior = prev_day_bars.get(symbol)

                signals = self.strategy.generate_signals(day_df, prior)
                day_trades, trade_capital_after, intraday_eq = self._simulate_day(
                    day_df, signals, trade_capital, consecutive_losses,
                )

                pnl_today = trade_capital_after - trade_capital
                capital += pnl_today

                for t in day_trades:
                    t["symbol"] = symbol
                    t["setup_score"] = setup.score
                    if t["pnl"] < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0

                all_trades.extend(day_trades)
                per_symbol_trades[symbol].extend(day_trades)

            equity_points.append({
                "t": int(pd.Timestamp(str(trade_date), tz="America/New_York")
                        .replace(hour=16).timestamp() * 1000),
                "v": round(capital, 2),
            })

            # Cache today's bars as prior day for tomorrow
            for symbol, day_df in day_bar_cache.items():
                prev_day_bars[symbol] = day_df

        # Build per-symbol results
        per_symbol_results = {}
        for symbol in all_bars:
            trades = per_symbol_trades[symbol]
            if trades:
                sym_metrics = calculate_metrics(trades, self.initial_capital / len(all_bars), [])
                per_symbol_results[symbol] = BacktestResult(
                    trades=trades, equity_curve=[], metrics=sym_metrics,
                )

        combined_metrics = calculate_metrics(all_trades, self.initial_capital, equity_points)

        result = PortfolioResult(
            per_symbol=per_symbol_results,
            combined_equity_curve=equity_points,
            combined_metrics=combined_metrics,
            total_capital=self.initial_capital,
        )
        # Attach selection log as extra data
        result.selection_log = selection_log  # type: ignore
        return result
