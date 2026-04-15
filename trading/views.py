import json

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from backtests.strategies import STRATEGY_CHOICES, STRATEGY_REGISTRY
from .models import LiveSession, LiveTrade, HYBRID_PRESETS
from .runner import LiveRunner, _running_sessions


def trading_index(request):
    sessions = LiveSession.objects.order_by("-created_at")[:20]
    return render(request, "trading/index.html", {
        "sessions": sessions,
        "strategy_choices": STRATEGY_CHOICES,
        "presets": HYBRID_PRESETS,
    })


@require_POST
def start_preset(request):
    """Start a session from a preset config (one-click deploy)."""
    preset_key = request.POST.get("preset", "ORB_HYBRID_50K")
    preset = HYBRID_PRESETS.get(preset_key)
    if not preset:
        return render(request, "trading/partials/error.html", {
            "error": f"Unknown preset: {preset_key}",
        })

    from django.conf import settings as django_settings
    blocked = getattr(django_settings, "BLOCKED_SYMBOLS", [])
    symbols = [s for s in preset["symbols"].keys() if s not in blocked]

    session = LiveSession.objects.create(
        strategy_name="ORB",
        symbols=symbols,
        parameters=preset,
        capital=preset["capital"],
        mode=LiveSession.Mode.HYBRID if preset.get("use_margin") else LiveSession.Mode.STOCK,
        use_margin=preset.get("use_margin", False),
        margin_multiplier=preset.get("margin_multiplier", 1.0),
        symbol_states={s: "AWAITING_OPEN" for s in symbols},
    )

    try:
        runner = LiveRunner(session.id)
        runner.start()
    except Exception as e:
        session.status = LiveSession.Status.FAILED
        session.error_message = str(e)
        session.save()
        return render(request, "trading/partials/error.html", {"error": str(e)})

    return render(request, "trading/partials/session_card.html", {
        "session": session,
        "is_running": True,
    })


@require_POST
def start_session(request):
    """Start a custom session."""
    symbols_raw = request.POST.get("symbols", "")
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    strategy_name = request.POST.get("strategy_name", "ORB")
    capital = float(request.POST.get("capital", 10000))
    mode = request.POST.get("mode", "stock")
    use_margin = request.POST.get("use_margin") == "on"

    if not symbols:
        return render(request, "trading/partials/error.html", {
            "error": "No symbols provided",
        })

    from django.conf import settings as django_settings
    blocked = getattr(django_settings, "BLOCKED_SYMBOLS", [])
    blocked_found = [s for s in symbols if s in blocked]
    if blocked_found:
        return render(request, "trading/partials/error.html", {
            "error": f"Blocked symbols: {', '.join(blocked_found)}",
        })

    strategy_cls = STRATEGY_REGISTRY.get(strategy_name)
    if not strategy_cls:
        return render(request, "trading/partials/error.html", {
            "error": f"Unknown strategy: {strategy_name}",
        })

    params = {}
    for spec in strategy_cls.parameter_specs():
        val = request.POST.get(spec.name)
        if val is not None:
            try:
                if spec.type == "int":
                    params[spec.name] = int(val)
                elif spec.type == "float":
                    params[spec.name] = float(val)
                elif spec.type == "categorical":
                    params[spec.name] = val == "True" if val in ("True", "False") else val
            except (ValueError, TypeError):
                params[spec.name] = spec.default
        else:
            params[spec.name] = spec.default

    session = LiveSession.objects.create(
        strategy_name=strategy_name,
        symbols=symbols,
        parameters=params,
        capital=capital,
        mode=LiveSession.Mode.HYBRID if mode == "hybrid" else LiveSession.Mode.STOCK,
        use_margin=use_margin,
        margin_multiplier=2.0 if use_margin else 1.0,
        symbol_states={s: "AWAITING_OPEN" for s in symbols},
    )

    try:
        runner = LiveRunner(session.id)
        runner.start()
    except Exception as e:
        session.status = LiveSession.Status.FAILED
        session.error_message = str(e)
        session.save()
        return render(request, "trading/partials/error.html", {"error": str(e)})

    return render(request, "trading/partials/session_card.html", {
        "session": session,
        "is_running": True,
    })


@require_POST
def stop_session(request, pk):
    session = get_object_or_404(LiveSession, pk=pk)
    runner = _running_sessions.get(pk)
    if runner:
        runner.stop()
    session.refresh_from_db()
    return render(request, "trading/partials/session_card.html", {
        "session": session,
        "is_running": False,
    })


def session_status(request, pk):
    """HTMX polling endpoint for live session status."""
    session = get_object_or_404(LiveSession, pk=pk)
    trades = list(session.trades.order_by("-created_at").values()[:50])
    is_running = pk in _running_sessions
    return render(request, "trading/partials/session_card.html", {
        "session": session,
        "trades": trades,
        "is_running": is_running,
    })
