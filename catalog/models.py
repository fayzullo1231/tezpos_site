from django.db import models
from accounts.models import TenantProfile


class Product(models.Model):
    UNIT_CHOICES = (
        ("dona", "dona"),
        ("kg", "kg"),
        ("litr", "litr"),
        ("quti", "quti"),
        ("metr", "metr"),
    )

    tenant = models.ForeignKey(TenantProfile, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=80, blank=True)
    barcode = models.CharField(max_length=80, blank=True)
    barcodes = models.JSONField(default=list, blank=True)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default="dona")
    category = models.CharField(max_length=120, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    wholesale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    expires_at = models.DateField(null=True, blank=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    image_url = models.URLField(blank=True)
    is_favorite = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def margin_pct(self) -> float:
        if self.cost_price and self.cost_price > 0:
            return float((self.selling_price - self.cost_price) / self.cost_price * 100)
        return 0.0

    @property
    def is_low_stock(self) -> bool:
        min_qty = self.min_stock if self.min_stock is not None else 0
        return self.stock_qty <= min_qty

    @property
    def barcode_list(self):
        codes = list(self.barcodes or [])
        if self.barcode and self.barcode not in codes:
            codes.insert(0, self.barcode)
        return codes

    @property
    def display_image(self) -> str:
        primary = self.images.filter(is_primary=True).first() or self.images.first()
        if primary:
            return primary.image.url
        if self.image:
            return self.image.url
        return self.image_url or ""


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/")
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "id"]

    def __str__(self) -> str:
        return f"{self.product_id} image {self.pk}"
