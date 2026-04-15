from django.conf import settings
from django.core.management.base import BaseCommand

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass

from securities.models import Security


class Command(BaseCommand):
    help = "Fetch securities (assets) from Alpaca and upsert into the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--asset-class",
            choices=["us_equity", "crypto"],
            default="us_equity",
            help="Asset class to fetch (default: us_equity)",
        )
        parser.add_argument(
            "--active-only",
            action="store_true",
            default=True,
            help="Only fetch active assets (default: True)",
        )

    def handle(self, *args, **options):
        client = TradingClient(
            settings.ALPACA_API_KEY,
            settings.ALPACA_SECRET_KEY,
            paper=settings.ALPACA_PAPER,
        )

        asset_class = (
            AssetClass.US_EQUITY
            if options["asset_class"] == "us_equity"
            else AssetClass.CRYPTO
        )

        request = GetAssetsRequest(asset_class=asset_class)
        assets = client.get_all_assets(request)

        if options["active_only"]:
            assets = [a for a in assets if a.status.value == "active"]

        self.stdout.write(f"Fetched {len(assets)} assets from Alpaca")

        created = 0
        updated = 0
        for asset in assets:
            _, was_created = Security.objects.update_or_create(
                symbol=asset.symbol,
                defaults={
                    "name": asset.name or "",
                    "exchange": asset.exchange.value if asset.exchange else "",
                    "asset_class": asset.asset_class.value if asset.asset_class else "",
                    "status": asset.status.value if asset.status else "active",
                    "tradable": asset.tradable,
                    "shortable": asset.shortable,
                    "fractionable": asset.fractionable,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: {created} created, {updated} updated")
        )
