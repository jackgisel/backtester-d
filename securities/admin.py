from django.contrib import admin
from .models import Security


@admin.register(Security)
class SecurityAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "exchange", "asset_class", "status", "tradable")
    list_filter = ("exchange", "asset_class", "status", "tradable")
    search_fields = ("symbol", "name")
