from django.core.management.base import BaseCommand

from securities.models import Security
from prices.models import Indicator


class Command(BaseCommand):
    help = "Compute technical indicators (SMA, RSI, MACD) for securities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbols",
            nargs="+",
            help="Specific symbols to compute (default: all with price data)",
        )

    def handle(self, *args, **options):
        if options["symbols"]:
            securities = Security.objects.filter(symbol__in=options["symbols"])
        else:
            securities = Security.objects.filter(prices__isnull=False).distinct()

        total = securities.count()
        if not total:
            self.stdout.write(self.style.WARNING("No securities with price data"))
            return

        for i, security in enumerate(securities, 1):
            self.stdout.write(f"[{i}/{total}] Computing indicators for {security.symbol}...")

            indicators = Indicator.compute_for_security(security)

            # Wipe old indicators for this security and bulk insert new ones
            Indicator.objects.filter(security=security).delete()
            Indicator.objects.bulk_create(indicators, batch_size=5000)

            self.stdout.write(f"  -> {len(indicators)} indicator values")

        self.stdout.write(self.style.SUCCESS(f"Done: computed indicators for {total} securities"))
