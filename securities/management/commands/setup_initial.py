"""One-time setup: superuser + securities.

Runs on first deploy. Safe to re-run (idempotent).
Intraday data is NOT fetched here — live trading streams directly from Alpaca.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Initial setup: create superuser and fetch securities"

    def handle(self, *args, **options):
        self._create_superuser()
        self._fetch_securities()

    def _create_superuser(self):
        User = get_user_model()
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not password:
            self.stdout.write(self.style.WARNING(
                "Skipping superuser: set DJANGO_SUPERUSER_PASSWORD env var"
            ))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'"))

    def _fetch_securities(self):
        from securities.models import Security

        if Security.objects.count() > 100:
            self.stdout.write(f"Securities already loaded ({Security.objects.count()})")
            return

        self.stdout.write("Fetching securities from Alpaca...")
        call_command("fetch_securities")
