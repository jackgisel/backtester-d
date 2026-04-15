from .orb import OpeningRangeBreakout
from .vwap_reclaim import VWAPReclaim
from .pdhl import PriorDayHLBreakout
from .momentum import MomentumContinuation

STRATEGY_REGISTRY: dict[str, type] = {
    "ORB": OpeningRangeBreakout,
    "VWAP_RECLAIM": VWAPReclaim,
    "PDHL": PriorDayHLBreakout,
    "MOMENTUM": MomentumContinuation,
}

STRATEGY_CHOICES = [(k, v.display_name) for k, v in STRATEGY_REGISTRY.items()]
