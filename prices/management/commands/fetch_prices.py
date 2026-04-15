from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from securities.models import Security
from prices.models import Price


class Command(BaseCommand):
    help = "Fetch historical daily prices from Alpaca for securities in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbols",
            nargs="+",
            help="Specific symbols to fetch (default: all securities in DB)",
        )
        parser.add_argument(
            "--start",
            type=str,
            help="Start date YYYY-MM-DD (default: 1 year ago)",
        )
        parser.add_argument(
            "--end",
            type=str,
            help="End date YYYY-MM-DD (default: today)",
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
            else end_date - timedelta(days=365)
        )

        if options["symbols"]:
            securities = Security.objects.filter(symbol__in=options["symbols"])
        else:
            securities = Security.objects.filter(status="active")

        symbols = list(securities.values_list("symbol", flat=True))
        if not symbols:
            self.stdout.write(self.style.WARNING("No securities found in database"))
            return

        # Process in batches of 100 symbols (Alpaca limit)
        batch_size = 100
        total_created = 0
        total_updated = 0

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            self.stdout.write(f"Fetching prices for {len(batch)} symbols...")

            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start_date,
                end=end_date,
            )
            bars = client.get_stock_bars(request)

            security_map = {
                s.symbol: s
                for s in Security.objects.filter(symbol__in=batch)
            }

            for symbol, symbol_bars in bars.data.items():
                security = security_map.get(symbol)
                if not security:
                    continue

                for bar in symbol_bars:
                    _, was_created = Price.objects.update_or_create(
                        security=security,
                        date=bar.timestamp.date(),
                        defaults={
                            "open": Decimal(str(bar.open)),
                            "high": Decimal(str(bar.high)),
                            "low": Decimal(str(bar.low)),
                            "close": Decimal(str(bar.close)),
                            "volume": bar.volume,
                            "vwap": Decimal(str(bar.vwap)) if bar.vwap else None,
                            "trade_count": bar.trade_count,
                        },
                    )
                    if was_created:
                        total_created += 1
                    else:
                        total_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {total_created} created, {total_updated} updated"
            )
        )
