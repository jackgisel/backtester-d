"""Daily cron job: fetch latest intraday bars for trading symbols.

Run via Railway cron or system cron at 5:00 PM ET (after market close).
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


TRADING_SYMBOLS = ["AVGO", "GOOGL", "CRM", "META"]


class Command(BaseCommand):
    help = "Daily refresh: fetch today's intraday bars for trading symbols"

    def handle(self, *args, **options):
        self.stdout.write("Fetching latest intraday bars...")
        call_command("fetch_intraday", symbols=TRADING_SYMBOLS, days=3)
        self.stdout.write(self.style.SUCCESS("Daily refresh complete"))
