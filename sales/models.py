from django.db import models
from accounts.models import TenantProfile
from catalog.models import Product


class Sale(models.Model):
    tenant = models.ForeignKey(TenantProfile, on_delete=models.CASCADE, related_name="sales")
    created_at = models.DateTimeField(auto_now_add=True)
    customer_name = models.CharField(max_length=160, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=30, default="cash")

    class Meta:
        ordering = ["-created_at"]

    @property
    def profit(self):
        return self.total_amount - self.total_cost


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def line_total(self):
        return self.qty * self.unit_price

# Create your models here.
