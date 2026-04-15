import numpy as np

RISK_FREE_ANNUAL = 0.05  # 5% annual risk-free rate
RISK_FREE_DAILY = RISK_FREE_ANNUAL / 252


def calculate_metrics(
    trades: list[dict],
    initial_capital: float,
    equity_curve: list[dict],
    intraday_equity_high: float | None = None,
    intraday_equity_low: float | None = None,
) -> dict:
    """Calculate backtest performance metrics from trade results.

    Sharpe and Sortino are computed from trade-level returns (not daily
    equity snapshots) to avoid inflation from zero-return flat days.
    Max drawdown uses intraday equity extremes when available.
    """
    empty = {
        "total_return_pct": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "max_drawdown_pct": None,
        "win_rate_pct": None,
        "profit_factor": None,
        "total_trades": 0,
        "avg_trade_duration_minutes": None,
    }

    if not trades:
        return empty

    pnls = np.array([t["pnl"] for t in trades])
    pnl_pcts = np.array([t["pnl_pct"] for t in trades]) / 100  # convert to decimal
    winners = pnls[pnls > 0]
    losers = pnls[pnls < 0]

    final_equity = equity_curve[-1]["v"] if equity_curve else initial_capital
    total_return_pct = ((final_equity - initial_capital) / initial_capital) * 100

    # Sharpe ratio from trade returns (not daily equity snapshots)
    # Annualize based on trades/day frequency
    n_days = len(equity_curve) if equity_curve else 1
    trades_per_day = len(trades) / max(n_days, 1)
    annualization = np.sqrt(252 * max(trades_per_day, 1))

    excess_returns = pnl_pcts - RISK_FREE_DAILY
    std = np.std(excess_returns)
    sharpe = float(np.mean(excess_returns) / std * annualization) if std > 0 else 0.0

    # Sortino ratio (only penalize downside deviation)
    downside = excess_returns[excess_returns < 0]
    downside_std = np.sqrt(np.mean(downside**2)) if len(downside) > 0 else 0.0
    sortino = float(
        np.mean(excess_returns) / downside_std * annualization
    ) if downside_std > 0 else 0.0

    # Max drawdown from equity curve (uses intraday tracking when available)
    values = np.array([initial_capital] + [e["v"] for e in equity_curve])
    peaks = np.maximum.accumulate(values)
    drawdowns = (peaks - values) / peaks * 100
    max_drawdown_pct = float(np.max(drawdowns))

    # Win rate
    win_rate = (len(winners) / len(pnls)) * 100

    # Profit factor
    gross_profit = float(np.sum(winners)) if len(winners) > 0 else 0.0
    gross_loss = float(np.abs(np.sum(losers))) if len(losers) > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 9999.0

    # Average trade duration
    durations = [t["duration_minutes"] for t in trades if t.get("duration_minutes")]
    avg_duration = float(np.mean(durations)) if durations else None

    return {
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "win_rate_pct": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "total_trades": len(trades),
        "avg_trade_duration_minutes": round(avg_duration, 2) if avg_duration else None,
    }
