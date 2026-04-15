from django.contrib import admin
from .models import IntradayBar, Price, Indicator


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ("security", "date", "open", "high", "low", "close", "volume")
    list_filter = ("date", "security__exchange")
    search_fields = ("security__symbol",)
    raw_id_fields = ("security",)


@admin.register(IntradayBar)
class IntradayBarAdmin(admin.ModelAdmin):
    list_display = ("security", "timestamp", "open", "high", "low", "close", "volume")
    list_filter = ("security__exchange",)
    search_fields = ("security__symbol",)
    raw_id_fields = ("security",)


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = ("security", "date", "name", "value")
    list_filter = ("name", "date")
    search_fields = ("security__symbol",)
    raw_id_fields = ("security",)
