"""Hybrid live trading runner: stock + 1DTE options on top signals."""
import asyncio
import logging
import threading
from datetime import datetime, timedelta

import pytz
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from alpaca.data.live import StockDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from .models import LiveSession, LiveTrade, HYBRID_PRESETS
from .state import BarData, LiveORBState, State

logger = logging.getLogger("trading.live")
EASTERN = pytz.timezone("America/New_York")

_running_sessions: dict[int, "LiveRunner"] = {}


class LiveRunner:
    """Manages a hybrid live trading session.

    Coordinates across symbols: after opening range, scores all setups,
    picks the top signals, routes best signals to options, rest to stock.
    """

    def __init__(self, session_id: int):
        self.session_id = session_id
        self.session = LiveSession.objects.get(id=session_id)
        self.trading_client = TradingClient(
            settings.ALPACA_API_KEY,
            settings.ALPACA_SECRET_KEY,
            paper=settings.ALPACA_PAPER,
        )
        self.data_stream = StockDataStream(
            settings.ALPACA_API_KEY,
            settings.ALPACA_SECRET_KEY,
        )
        self.states: dict[str, LiveORBState] = {}
        self._loop = None
        self._scoring_done = False
        self._max_trades = self.session.parameters.get("max_trades_per_day", 4)
        self._options_top_pct = self.session.parameters.get("options_on_top_pct", 30)
        self._trades_placed = 0

        # Initialize per-symbol state machines with Kelly-weighted capital
        effective_capital = self.session.effective_capital
        sym_configs = self.session.parameters.get("symbols", {})

        for symbol in self.session.symbols:
            sym_cfg = sym_configs.get(symbol, {})
            weight = sym_cfg.get("weight", 1.0 / len(self.session.symbols))
            params = sym_cfg.get("params", self.session.parameters)

            self.states[symbol] = LiveORBState(
                symbol=symbol,
                opening_range_minutes=params.get("opening_range_minutes", 15),
                stop_loss_pct=params.get("stop_loss_pct", 1.0),
                take_profit_pct=params.get("take_profit_pct", 2.0),
                use_atr_stops=params.get("use_atr_stops", False),
                atr_stop_mult=params.get("atr_stop_mult", 1.5),
                atr_tp_mult=params.get("atr_tp_mult", 4.0),
                volume_threshold=params.get("volume_threshold", 1.0),
                entry_cutoff_minutes=params.get("entry_cutoff_minutes", 180),
                use_trend_filter=params.get("use_trend_filter", True),
                capital=effective_capital * weight,
                position_size_pct=0.50,
            )

    async def _on_bar(self, bar):
        """Callback for each 1-minute bar from Alpaca websocket."""
        symbol = bar.symbol
        state = self.states.get(symbol)
        if not state:
            return

        bar_data = BarData(
            timestamp=bar.timestamp,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

        action = state.on_bar(bar_data)
        if action is None:
            return

        await self._update_states_db()

        if action["action"] == "scored":
            await self._handle_scoring()
        elif action["action"] in ("enter_long", "enter_short"):
            if self._trades_placed < self._max_trades:
                await self._execute_entry(symbol, action)
        elif action["action"] == "exit":
            await self._flatten_symbol(symbol, action.get("reason", "signal"))

    async def _handle_scoring(self):
        """After all symbols have scored, pick the best ones."""
        # Check if all symbols have reached SCORING state
        all_scored = all(
            s.state in (State.SCORING, State.DONE)
            for s in self.states.values()
        )
        if not all_scored or self._scoring_done:
            return

        self._scoring_done = True

        # Rank by score
        scored = [
            (sym, s.setup_score)
            for sym, s in self.states.items()
            if s.state == State.SCORING and s.setup_score > 0
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Approve top N
        approved = scored[:self._max_trades]
        for sym, score in approved:
            self.states[sym].approve()

        # Reject the rest
        approved_syms = {sym for sym, _ in approved}
        for sym, s in self.states.items():
            if s.state == State.SCORING and sym not in approved_syms:
                s.reject()

        logger.info(
            f"Scoring complete: approved={[f'{s}({sc:.0f})' for s,sc in approved]}, "
            f"rejected={[s for s in self.states if s not in approved_syms and self.states[s].setup_score > 0]}"
        )
        await self._update_states_db()

    async def _execute_entry(self, symbol: str, action: dict):
        """Execute entry — options for top signals, stock for the rest."""
        is_hybrid = self.session.mode == LiveSession.Mode.HYBRID
        score = action.get("score", 0)

        # Top signals get options in hybrid mode
        use_options = False
        if is_hybrid and self._options_top_pct > 0:
            all_scores = sorted(
                [s.setup_score for s in self.states.values() if s.setup_score > 0],
                reverse=True,
            )
            if all_scores:
                threshold_idx = max(1, int(len(all_scores) * self._options_top_pct / 100))
                options_threshold = all_scores[min(threshold_idx, len(all_scores) - 1)]
                use_options = score >= options_threshold

        if use_options:
            await self._submit_options_order(symbol, action)
        else:
            await self._submit_bracket_order(symbol, action)

        self._trades_placed += 1

    async def _submit_bracket_order(self, symbol: str, action: dict):
        """Submit a stock bracket order (entry + stop + TP)."""
        side = OrderSide.BUY if "long" in action["action"] else OrderSide.SELL
        shares = action["shares"]

        try:
            order = self.trading_client.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=shares,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    order_class="bracket",
                    stop_loss={"stop_price": action["stop"]},
                    take_profit={"limit_price": action["tp"]},
                )
            )
            logger.info(f"[{symbol}] STOCK bracket order: {order.id} ({shares} shares)")

            await sync_to_async(LiveTrade.objects.create)(
                session=self.session,
                symbol=symbol,
                instrument_type=LiveTrade.InstrumentType.STOCK,
                direction="long" if side == OrderSide.BUY else "short",
                entry_price=action["price"],
                shares=shares,
                alpaca_order_id=str(order.id),
                entry_time=timezone.now(),
            )
        except Exception as e:
            logger.error(f"[{symbol}] Stock order failed: {e}")

    async def _submit_options_order(self, symbol: str, action: dict):
        """Submit a 1DTE ITM option order for amplified returns."""
        direction = "long" if "long" in action["action"] else "short"
        entry_price = action["price"]

        try:
            # Get option chain for 1DTE
            from alpaca.trading.requests import GetOptionContractsRequest
            from datetime import date

            tomorrow = date.today() + timedelta(days=1)
            # Find the nearest expiry that's at least 1 day out
            option_type = "call" if direction == "long" else "put"

            # Use ITM strike: slightly in the money for higher delta
            if direction == "long":
                target_strike = entry_price * 0.99  # 1% ITM
            else:
                target_strike = entry_price * 1.01

            # For now, fall back to stock if options chain lookup fails
            # Alpaca options API requires specific contract symbols
            # Format: SYMBOL + YYMMDD + C/P + strike*1000 (OCC format)
            expiry_str = tomorrow.strftime("%y%m%d")
            strike_int = int(round(target_strike))
            cp = "C" if direction == "long" else "P"
            # Pad symbol to 6 chars
            padded_sym = symbol.ljust(6)
            option_symbol = f"{padded_sym}{expiry_str}{cp}{strike_int * 1000:08d}"

            # Size: use ~10-15% of symbol capital for option premium
            state = self.states[symbol]
            option_budget = state.capital * 0.15
            # Estimate premium at ~$3-5 per contract for ITM 1DTE
            est_premium = entry_price * 0.02  # ~2% of stock price
            contracts = max(1, int(option_budget / (est_premium * 100)))

            order = self.trading_client.submit_order(
                MarketOrderRequest(
                    symbol=option_symbol,
                    qty=contracts,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )
            logger.info(
                f"[{symbol}] OPTION order: {option_symbol} x{contracts} ({order.id})"
            )

            await sync_to_async(LiveTrade.objects.create)(
                session=self.session,
                symbol=symbol,
                instrument_type=LiveTrade.InstrumentType.OPTION,
                direction=direction,
                entry_price=est_premium,
                contracts=contracts,
                option_symbol=option_symbol,
                alpaca_order_id=str(order.id),
                entry_time=timezone.now(),
            )
        except Exception as e:
            logger.warning(f"[{symbol}] Options order failed, falling back to stock: {e}")
            await self._submit_bracket_order(symbol, action)

    async def _flatten_symbol(self, symbol: str, reason: str):
        """Close any open position for a symbol."""
        try:
            position = self.trading_client.get_open_position(symbol)
            if position:
                side = OrderSide.SELL if float(position.qty) > 0 else OrderSide.BUY
                self.trading_client.submit_order(
                    MarketOrderRequest(
                        symbol=symbol,
                        qty=abs(float(position.qty)),
                        side=side,
                        time_in_force=TimeInForce.DAY,
                    )
                )
                logger.info(f"[{symbol}] Flattened: {reason}")
        except Exception as e:
            logger.warning(f"[{symbol}] Flatten failed: {e}")

    async def _update_states_db(self):
        """Persist symbol states to DB for UI polling."""
        try:
            await sync_to_async(self.session.refresh_from_db)()
            self.session.symbol_states = {
                sym: s.state.value for sym, s in self.states.items()
            }
            self.session.trades_today = self._trades_placed
            await sync_to_async(self.session.save)(update_fields=["symbol_states", "trades_today"])
        except Exception:
            pass

    async def _save_session(self, **kwargs):
        """Helper to save session fields from async context."""
        for k, v in kwargs.items():
            setattr(self.session, k, v)
        fields = list(kwargs.keys())
        await sync_to_async(self.session.save)(update_fields=fields)

    async def _run(self):
        """Main async loop."""
        await self._save_session(status=LiveSession.Status.RUNNING)

        self.data_stream.subscribe_bars(self._on_bar, *self.session.symbols)

        try:
            await self.data_stream._run_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Stream error: {e}")
            await self._save_session(
                status=LiveSession.Status.FAILED, error_message=str(e),
            )

    def start(self):
        """Start the runner in a background thread."""
        def _thread_target():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run())
            finally:
                self._loop.close()

        _running_sessions[self.session_id] = self
        thread = threading.Thread(target=_thread_target, daemon=True)
        thread.start()
        logger.info(f"Session {self.session_id} started (mode={self.session.mode})")

    def stop(self):
        """Stop the runner gracefully — flatten everything."""
        logger.info(f"Stopping session {self.session_id}")

        for symbol in self.session.symbols:
            try:
                position = self.trading_client.get_open_position(symbol)
                if position:
                    side = OrderSide.SELL if float(position.qty) > 0 else OrderSide.BUY
                    self.trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=symbol,
                            qty=abs(float(position.qty)),
                            side=side,
                            time_in_force=TimeInForce.DAY,
                        )
                    )
            except Exception as e:
                logger.warning(f"[{symbol}] Flatten on stop: {e}")

        try:
            self.trading_client.cancel_orders()
        except Exception:
            pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        self.session.status = LiveSession.Status.STOPPED
        self.session.stopped_at = timezone.now()

        try:
            account = self.trading_client.get_account()
            self.session.total_pnl = float(account.equity) - float(self.session.capital)
        except Exception:
            pass

        self.session.save()
        _running_sessions.pop(self.session_id, None)
