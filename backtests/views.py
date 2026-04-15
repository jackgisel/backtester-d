import json
import uuid

from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from securities.models import Security

from .engine import BacktestEngine
from .forms import BacktestForm, OptimizationForm
from .models import Backtest, OptimizationRun, Trade
from .optimizer import cross_validate_params, run_optimization, run_walk_forward
from .strategies import STRATEGY_REGISTRY


def index(request):
    backtest_form = BacktestForm()
    opt_form = OptimizationForm()
    recent = Backtest.objects.select_related("security").order_by("-created_at")[:20]
    return render(request, "backtests/index.html", {
        "backtest_form": backtest_form,
        "opt_form": opt_form,
        "recent": recent,
    })


@require_POST
def run_backtest(request):
    form = BacktestForm(request.POST)
    if not form.is_valid():
        return render(request, "backtests/partials/form_errors.html", {"form": form})

    data = form.cleaned_data
    strategy_cls = STRATEGY_REGISTRY[data["strategy_name"]]

    # Build strategy params from form
    params = {}
    for spec in strategy_cls.parameter_specs():
        val = data.get(spec.name)
        if val is not None:
            params[spec.name] = val
        else:
            params[spec.name] = spec.default

    # Support comma-separated multi-symbol input
    symbols = [s.strip().upper() for s in data["symbol"].split(",") if s.strip()]

    if len(symbols) > 1:
        return _run_portfolio_backtest(request, symbols, data, strategy_cls, params)

    # Single-symbol backtest
    try:
        security = Security.objects.get(symbol=symbols[0])
    except Security.DoesNotExist:
        return render(request, "backtests/partials/error.html", {
            "error": f"Security '{symbols[0]}' not found",
        })

    backtest = Backtest.objects.create(
        security=security,
        strategy_name=data["strategy_name"],
        parameters=params,
        start_date=data["start_date"],
        end_date=data["end_date"],
        initial_capital=data["initial_capital"],
        status=Backtest.Status.RUNNING,
    )

    try:
        strategy = strategy_cls(**params)
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=data["initial_capital"],
        )
        result = engine.run(security, data["start_date"], data["end_date"])

        # Save trades
        Trade.objects.bulk_create([
            Trade(backtest=backtest, **t) for t in result.trades
        ])

        # Save metrics
        for key, val in result.metrics.items():
            if hasattr(backtest, key):
                setattr(backtest, key, val)
        backtest.equity_curve = result.equity_curve
        backtest.status = Backtest.Status.COMPLETED
        backtest.completed_at = timezone.now()
        backtest.save()

    except Exception as e:
        backtest.status = Backtest.Status.FAILED
        backtest.error_message = str(e)
        backtest.save(update_fields=["status", "error_message"])
        return render(request, "backtests/partials/error.html", {"error": str(e)})

    trades = list(backtest.trades.order_by("entry_timestamp").values())
    return render(request, "backtests/partials/results_panel.html", {
        "backtest": backtest,
        "trades": trades,
        "equity_curve_json": json.dumps(backtest.equity_curve or []),
    })


def _run_portfolio_backtest(request, symbols, data, strategy_cls, params):
    """Handle multi-symbol portfolio backtest."""
    securities = list(Security.objects.filter(symbol__in=symbols))
    found = {s.symbol for s in securities}
    missing = [s for s in symbols if s not in found]
    if missing:
        return render(request, "backtests/partials/error.html", {
            "error": f"Securities not found: {', '.join(missing)}",
        })

    try:
        strategy = strategy_cls(**params)
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=data["initial_capital"],
        )
        portfolio = engine.run_portfolio(securities, data["start_date"], data["end_date"])
    except Exception as e:
        return render(request, "backtests/partials/error.html", {"error": str(e)})

    # Save individual backtests for each symbol
    for sym, result in portfolio.per_symbol.items():
        sec = next(s for s in securities if s.symbol == sym)
        bt = Backtest.objects.create(
            security=sec,
            strategy_name=data["strategy_name"],
            parameters=params,
            start_date=data["start_date"],
            end_date=data["end_date"],
            initial_capital=round(data["initial_capital"] / len(securities), 2),
            status=Backtest.Status.COMPLETED,
            completed_at=timezone.now(),
            equity_curve=result.equity_curve,
        )
        for key, val in result.metrics.items():
            if hasattr(bt, key):
                setattr(bt, key, val)
        bt.save()
        Trade.objects.bulk_create([Trade(backtest=bt, **t) for t in result.trades])

    return render(request, "backtests/partials/portfolio_results.html", {
        "portfolio": portfolio,
        "symbols": symbols,
        "strategy_name": data["strategy_name"],
        "equity_curve_json": json.dumps(portfolio.combined_equity_curve),
    })


def backtest_detail(request, pk):
    backtest = get_object_or_404(Backtest, pk=pk)
    trades = list(backtest.trades.order_by("entry_timestamp").values())
    return render(request, "backtests/results.html", {
        "backtest": backtest,
        "trades": trades,
        "equity_curve_json": json.dumps(backtest.equity_curve or []),
    })


@require_POST
def run_optimization_view(request):
    form = OptimizationForm(request.POST)
    if not form.is_valid():
        return render(request, "backtests/partials/form_errors.html", {"form": form})

    data = form.cleaned_data
    try:
        security = Security.objects.get(symbol=data["symbol"].upper())
    except Security.DoesNotExist:
        return render(request, "backtests/partials/error.html", {
            "error": f"Security '{data['symbol']}' not found",
        })

    study_name = f"{data['strategy_name']}_{security.symbol}_{uuid.uuid4().hex[:8]}"

    opt_run = OptimizationRun.objects.create(
        security=security,
        strategy_name=data["strategy_name"],
        start_date=data["start_date"],
        end_date=data["end_date"],
        n_trials=data["n_trials"],
        objective_metric=data["objective_metric"],
        study_name=study_name,
    )

    try:
        run_optimization(opt_run.id)
    except Exception as e:
        return render(request, "backtests/partials/error.html", {"error": str(e)})

    opt_run.refresh_from_db()
    return render(request, "backtests/partials/optuna_results.html", {
        "opt_run": opt_run,
    })


@require_POST
def run_walk_forward_view(request):
    """Walk-forward optimization via HTMX."""
    symbol = request.POST.get("symbol", "").upper()
    strategy_name = request.POST.get("strategy_name", "ORB")
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")
    train_days = int(request.POST.get("train_days", 120))
    test_days = int(request.POST.get("test_days", 30))
    n_trials = int(request.POST.get("n_trials", 50))
    objective_metric = request.POST.get("objective_metric", "sharpe_ratio")

    try:
        security = Security.objects.get(symbol=symbol)
    except Security.DoesNotExist:
        return render(request, "backtests/partials/error.html", {
            "error": f"Security '{symbol}' not found",
        })

    from datetime import date as d
    try:
        result = run_walk_forward(
            security=security,
            strategy_name=strategy_name,
            total_start=d.fromisoformat(start_date),
            total_end=d.fromisoformat(end_date),
            train_days=train_days,
            test_days=test_days,
            n_trials=n_trials,
            objective_metric=objective_metric,
        )
    except Exception as e:
        return render(request, "backtests/partials/error.html", {"error": str(e)})

    return render(request, "backtests/partials/walk_forward_results.html", {
        "result": result,
        "symbol": symbol,
        "strategy_name": strategy_name,
    })


@require_POST
def run_cross_validation_view(request):
    """Cross-symbol validation via HTMX."""
    symbols_raw = request.POST.get("symbols", "")
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    strategy_name = request.POST.get("strategy_name", "ORB")
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")
    params_json = request.POST.get("params", "{}")

    import json as json_mod
    from datetime import date as d
    try:
        params = json_mod.loads(params_json)
    except json_mod.JSONDecodeError:
        return render(request, "backtests/partials/error.html", {
            "error": "Invalid params JSON",
        })

    try:
        result = cross_validate_params(
            params=params,
            strategy_name=strategy_name,
            symbols=symbols,
            start_date=d.fromisoformat(start_date),
            end_date=d.fromisoformat(end_date),
        )
    except Exception as e:
        return render(request, "backtests/partials/error.html", {"error": str(e)})

    return render(request, "backtests/partials/cross_validation_results.html", {
        "result": result,
        "strategy_name": strategy_name,
    })
