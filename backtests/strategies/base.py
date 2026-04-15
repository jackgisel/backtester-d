from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd


@dataclass
class ParameterSpec:
    name: str
    type: Literal["int", "float", "categorical"]
    default: Any
    min_value: Any = None
    max_value: Any = None
    choices: list[Any] = None
    step: Any = None
    description: str = ""


@dataclass
class Signal:
    timestamp: pd.Timestamp
    action: Literal["enter_long", "enter_short", "exit_long", "exit_short"]
    price: float
    metadata: dict = field(default_factory=dict)


@dataclass
class SetupScore:
    """Scores how favorable a symbol's setup is for trading today."""
    symbol: str
    score: float  # 0-100, higher = better
    direction: str  # "long" | "short" | "neutral"
    range_high: float = 0.0
    range_low: float = 0.0
    range_width_pct: float = 0.0
    volume_ratio: float = 0.0
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base for all trading strategies.

    Subclasses define parameter_specs() and generate_signals().
    The engine handles stop-loss/take-profit execution separately.
    """

    name: str = ""
    display_name: str = ""

    def __init__(self, **params):
        spec_map = {p.name: p for p in self.parameter_specs()}
        for param_name, value in params.items():
            if param_name not in spec_map:
                raise ValueError(f"Unknown parameter: {param_name}")
            setattr(self, param_name, value)
        # Apply defaults for unspecified params
        for spec in self.parameter_specs():
            if not hasattr(self, spec.name):
                setattr(self, spec.name, spec.default)

    @classmethod
    @abstractmethod
    def parameter_specs(cls) -> list[ParameterSpec]:
        """Declare all tunable parameters with types and bounds."""
        ...

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {p.name: p.default for p in cls.parameter_specs()}

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> list[Signal]:
        """Generate trading signals for a single trading day.

        Args:
            bars: DataFrame of 1-minute bars indexed by UTC DatetimeIndex.
                  Columns: open, high, low, close, volume, vwap

        Returns:
            List of Signal objects in chronological order.
        """
        ...

    def score_setup(self, bars: pd.DataFrame, prior_day_bars: pd.DataFrame | None = None) -> SetupScore | None:
        """Score the day's setup quality after the opening range forms.

        Override in subclasses to implement setup scoring.
        Return None if the symbol should not trade today.
        """
        return None

    def validate_params(self) -> list[str]:
        errors = []
        for spec in self.parameter_specs():
            val = getattr(self, spec.name)
            if spec.type in ("int", "float"):
                if spec.min_value is not None and val < spec.min_value:
                    errors.append(f"{spec.name} below minimum {spec.min_value}")
                if spec.max_value is not None and val > spec.max_value:
                    errors.append(f"{spec.name} above maximum {spec.max_value}")
        return errors
