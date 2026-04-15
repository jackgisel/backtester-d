from datetime import date, timedelta

import optuna
from django.utils import timezone

from securities.models import Security
from .engine import BacktestEngine
from .strategies import STRATEGY_REGISTRY
from .strategies.base import BaseStrategy

optuna.logging.set_verbosity(optuna.logging.WARNING)


def build_search_space(trial: optuna.Trial, strategy_cls: type[BaseStrategy]) -> dict:
    """Map ParameterSpec definitions to Optuna suggest_* calls."""
    params = {}
    for spec in strategy_cls.parameter_specs():
        if spec.type == "int":
            params[spec.name] = trial.suggest_int(
                spec.name, spec.min_value, spec.max_value, step=spec.step or 1,
            )
        elif spec.type == "float":
            params[spec.name] = trial.suggest_float(
                spec.name, spec.min_value, spec.max_value, step=spec.step,
            )
        elif spec.type == "categorical":
            params[spec.name] = trial.suggest_categorical(spec.name, spec.choices)
    return params


def run_optimization(optimization_run_id: int):
    """Run an Optuna study for the given OptimizationRun."""
    from .models import OptimizationRun

    opt_run = OptimizationRun.objects.get(id=optimization_run_id)
    opt_run.status = OptimizationRun.Status.RUNNING
    opt_run.save(update_fields=["status"])

    try:
        strategy_cls = STRATEGY_REGISTRY[opt_run.strategy_name]
        security = opt_run.security

        def objective(trial):
            params = build_search_space(trial, strategy_cls)
            strategy = strategy_cls(**params)
            engine = BacktestEngine(strategy=strategy)
            try:
                result = engine.run(security, opt_run.start_date, opt_run.end_date)
                metric = result.metrics.get(opt_run.objective_metric)
                if metric is None:
                    return -999.0
                return float(metric)
            except Exception:
                return -999.0

        study = optuna.create_study(
            study_name=opt_run.study_name,
            direction="maximize",
        )
        study.optimize(objective, n_trials=opt_run.n_trials)

        trials_data = [
            {
                "number": t.number,
                "params": t.params,
                "value": t.value,
                "state": t.state.name,
            }
            for t in study.trials
        ]

        opt_run.status = OptimizationRun.Status.COMPLETED
        opt_run.best_params = study.best_params
        opt_run.best_value = study.best_value
        opt_run.trials_data = trials_data
        opt_run.completed_at = timezone.now()
        opt_run.save()

    except Exception as e:
        opt_run.status = OptimizationRun.Status.FAILED
        opt_run.error_message = str(e)
        opt_run.save(update_fields=["status", "error_message"])
        raise


def run_walk_forward(
    security: Security,
    strategy_name: str,
    total_start: date,
    total_end: date,
    train_days: int = 120,
    test_days: int = 30,
    n_trials: int = 50,
    objective_metric: str = "sharpe_ratio",
) -> dict:
    """Walk-forward optimization: rolling train/test windows.

    Splits the date range into windows:
      [train_start..train_end] -> optimize
      [test_start..test_end]   -> test with best params (out-of-sample)
    Steps forward by test_days each iteration.

    Returns dict with per-window results and aggregated OOS metrics.
    """
    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    windows = []
    cursor = total_start

    while cursor + timedelta(days=train_days + test_days) <= total_end:
        train_start = cursor
        train_end = cursor + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days)

        # Optimize on training window
        def make_objective(t_start, t_end):
            def objective(trial):
                params = build_search_space(trial, strategy_cls)
                strategy = strategy_cls(**params)
                engine = BacktestEngine(strategy=strategy)
                try:
                    result = engine.run(security, t_start, t_end)
                    metric = result.metrics.get(objective_metric)
                    return float(metric) if metric is not None else -999.0
                except Exception:
                    return -999.0
            return objective

        study = optuna.create_study(direction="maximize")
        study.optimize(make_objective(train_start, train_end), n_trials=n_trials)
        best_params = study.best_params

        # Test on out-of-sample window
        strategy = strategy_cls(**best_params)
        engine = BacktestEngine(strategy=strategy)
        try:
            oos_result = engine.run(security, test_start, test_end)
            oos_metrics = oos_result.metrics
            oos_trades = oos_result.trades
        except Exception:
            oos_metrics = {}
            oos_trades = []

        windows.append({
            "train_start": str(train_start),
            "train_end": str(train_end),
            "test_start": str(test_start),
            "test_end": str(test_end),
            "best_params": best_params,
            "in_sample_value": study.best_value,
            "oos_metrics": oos_metrics,
            "oos_trades_count": len(oos_trades),
        })

        cursor += timedelta(days=test_days)

    # Aggregate OOS metrics
    oos_sharpes = [w["oos_metrics"].get("sharpe_ratio", 0) for w in windows if w["oos_metrics"]]
    oos_returns = [w["oos_metrics"].get("total_return_pct", 0) for w in windows if w["oos_metrics"]]
    oos_trades_total = sum(w["oos_trades_count"] for w in windows)

    import numpy as np
    return {
        "windows": windows,
        "n_windows": len(windows),
        "avg_oos_sharpe": round(float(np.mean(oos_sharpes)), 4) if oos_sharpes else None,
        "avg_oos_return_pct": round(float(np.mean(oos_returns)), 4) if oos_returns else None,
        "total_oos_trades": oos_trades_total,
    }


def cross_validate_params(
    params: dict,
    strategy_name: str,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict:
    """Run fixed params across multiple symbols without re-optimizing.

    Tests whether an edge generalizes beyond the symbol it was optimized on.
    """
    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    results = {}

    for symbol in symbols:
        try:
            security = Security.objects.get(symbol=symbol.upper())
        except Security.DoesNotExist:
            results[symbol] = {"error": "Symbol not found"}
            continue

        strategy = strategy_cls(**params)
        engine = BacktestEngine(strategy=strategy)
        try:
            result = engine.run(security, start_date, end_date)
            results[symbol] = {
                "metrics": result.metrics,
                "n_trades": len(result.trades),
            }
        except Exception as e:
            results[symbol] = {"error": str(e)}

    # Aggregate across symbols that succeeded
    import numpy as np
    valid = [r for r in results.values() if "metrics" in r]
    sharpes = [r["metrics"].get("sharpe_ratio", 0) for r in valid]
    returns = [r["metrics"].get("total_return_pct", 0) for r in valid]

    return {
        "per_symbol": results,
        "symbols_tested": len(symbols),
        "symbols_succeeded": len(valid),
        "avg_sharpe": round(float(np.mean(sharpes)), 4) if sharpes else None,
        "avg_return_pct": round(float(np.mean(returns)), 4) if returns else None,
    }
