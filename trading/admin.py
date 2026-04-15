from django.contrib import admin
from .models import LiveSession, LiveTrade


@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = ("strategy_name", "symbols", "status", "total_pnl", "created_at")
    list_filter = ("strategy_name", "status")


@admin.register(LiveTrade)
class LiveTradeAdmin(admin.ModelAdmin):
    list_display = ("session", "symbol", "direction", "entry_price", "exit_price", "pnl", "created_at")
    list_filter = ("direction", "symbol")
