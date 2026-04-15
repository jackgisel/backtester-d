from django_bolt import BoltAPI
import msgspec

from securities.models import Security
from prices.models import Indicator, Price

api = BoltAPI(prefix="/api")


class SecuritySchema(msgspec.Struct):
    id: int
    symbol: str
    name: str
    exchange: str
    asset_class: str
    status: str
    tradable: bool
    shortable: bool
    fractionable: bool


class PriceSchema(msgspec.Struct):
    id: int
    security_symbol: str
    date: str
    open: str
    high: str
    low: str
    close: str
    volume: int
    vwap: str | None
    trade_count: int | None


class IndicatorSchema(msgspec.Struct):
    date: str
    name: str
    value: str | None


@api.get("/securities")
async def list_securities() -> list[SecuritySchema]:
    securities = []
    async for s in Security.objects.all():
        securities.append(
            SecuritySchema(
                id=s.id,
                symbol=s.symbol,
                name=s.name,
                exchange=s.exchange,
                asset_class=s.asset_class,
                status=s.status,
                tradable=s.tradable,
                shortable=s.shortable,
                fractionable=s.fractionable,
            )
        )
    return securities


@api.get("/securities/{symbol}")
async def get_security(symbol: str) -> SecuritySchema:
    s = await Security.objects.aget(symbol=symbol.upper())
    return SecuritySchema(
        id=s.id,
        symbol=s.symbol,
        name=s.name,
        exchange=s.exchange,
        asset_class=s.asset_class,
        status=s.status,
        tradable=s.tradable,
        shortable=s.shortable,
        fractionable=s.fractionable,
    )


@api.get("/securities/{symbol}/prices")
async def list_prices(symbol: str) -> list[PriceSchema]:
    security = await Security.objects.aget(symbol=symbol.upper())
    prices = []
    async for p in Price.objects.filter(security=security).select_related("security"):
        prices.append(
            PriceSchema(
                id=p.id,
                security_symbol=security.symbol,
                date=str(p.date),
                open=str(p.open),
                high=str(p.high),
                low=str(p.low),
                close=str(p.close),
                volume=p.volume,
                vwap=str(p.vwap) if p.vwap else None,
                trade_count=p.trade_count,
            )
        )
    return prices


@api.get("/securities/{symbol}/indicators")
async def list_indicators(symbol: str, name: str = "") -> list[IndicatorSchema]:
    security = await Security.objects.aget(symbol=symbol.upper())
    qs = Indicator.objects.filter(security=security)
    if name:
        qs = qs.filter(name=name)
    indicators = []
    async for ind in qs:
        indicators.append(
            IndicatorSchema(
                date=str(ind.date),
                name=ind.name,
                value=str(ind.value) if ind.value is not None else None,
            )
        )
    return indicators
