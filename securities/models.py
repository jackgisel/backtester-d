from django.db import models


class Security(models.Model):
    symbol = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    exchange = models.CharField(max_length=50, blank=True)
    asset_class = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, default="active")
    tradable = models.BooleanField(default=True)
    shortable = models.BooleanField(default=False)
    fractionable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "securities"
        ordering = ["symbol"]

    def __str__(self):
        return f"{self.symbol} - {self.name}"
