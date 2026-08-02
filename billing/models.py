from django.db import models
from accounts.models import TenantProfile


class Plan(models.Model):
    name = models.CharField(max_length=120)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2)
    max_users = models.PositiveIntegerField(default=1)
    max_products = models.PositiveIntegerField(default=5000)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.monthly_price})"


class Subscription(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("paused", "Paused"),
        ("expired", "Expired"),
    )
    tenant = models.OneToOneField(
        TenantProfile, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    starts_at = models.DateField()
    ends_at = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    auto_renew = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.tenant.business_name} - {self.plan.name}"

# Create your models here.
