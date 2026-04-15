from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from securities.models import Security
from prices.models import IntradayBar


class Command(BaseCommand):
    help = "Fetch 1-minute intraday bars from Alpaca"

    def add_arguments(self, parser):
        parser.add_argument("--symbols", nargs="+", required=True)
        parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
        parser.add_argument("--end", type=str, help="End date YYYY-MM-DD")
        parser.add_argument(
            "--days", type=int, default=30,
            help="Days of history if no --start given (default: 30)",
        )

    def handle(self, *args, **options):
        client = StockHistoricalDataClient(
            settings.ALPACA_API_KEY,
            settings.ALPACA_SECRET_KEY,
        )

        end_date = (
            date.fromisoformat(options["end"]) if options["end"] else date.today()
        )
        start_date = (
            date.fromisoformat(options["start"])
            if options["start"]
            else end_date - timedelta(days=options["days"])
        )

        securities = Security.objects.filter(symbol__in=options["symbols"])
        symbols = list(securities.values_list("symbol", flat=True))
        if not symbols:
            self.stdout.write(self.style.WARNING("No matching securities found"))
            return

        self.stdout.write(
            f"Fetching 1-min bars for {symbols} from {start_date} to {end_date}"
        )

        # Smaller batches for minute data (large payloads)
        batch_size = 10
        total_processed = 0

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            self.stdout.write(f"  Batch: {batch}...")

            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Minute,
                start=start_date,
                end=end_date,
            )
            bars = client.get_stock_bars(request)
            security_map = {
                s.symbol: s for s in Security.objects.filter(symbol__in=batch)
            }

            to_create = []
            for symbol, symbol_bars in bars.data.items():
                security = security_map.get(symbol)
                if not security:
                    continue
                for bar in symbol_bars:
                    to_create.append(
                        IntradayBar(
                            security=security,
                            timestamp=bar.timestamp,
                            open=Decimal(str(bar.open)),
                            high=Decimal(str(bar.high)),
                            low=Decimal(str(bar.low)),
                            close=Decimal(str(bar.close)),
                            volume=bar.volume,
                            vwap=Decimal(str(bar.vwap)) if bar.vwap else None,
                            trade_count=bar.trade_count,
                        )
                    )

            if to_create:
                IntradayBar.objects.bulk_create(
                    to_create,
                    update_conflicts=True,
                    update_fields=[
                        "open", "high", "low", "close", "volume", "vwap", "trade_count",
                    ],
                    unique_fields=["security", "timestamp"],
                )
            total_processed += len(to_create)
            self.stdout.write(f"  -> {len(to_create)} bars")

        self.stdout.write(
            self.style.SUCCESS(f"Done: {total_processed} total bars processed")
        )
