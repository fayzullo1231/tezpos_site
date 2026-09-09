from django.contrib import admin

from .models import BarcodeCatalog, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "tenant", "unit", "selling_price", "is_active")
    list_filter = ("unit", "is_active")
    search_fields = ("name", "barcode", "sku")


@admin.register(BarcodeCatalog)
class BarcodeCatalogAdmin(admin.ModelAdmin):
    """Faqat Django staff/superuser — /admin/ orqali."""

    change_form_template = "admin/catalog/barcodecatalog/change_form.html"
    list_display = ("barcode", "name", "unit", "updated_at", "created_at")
    list_filter = ("unit",)
    search_fields = ("barcode", "name", "note")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-updated_at",)
    fieldsets = (
        (
            None,
            {
                "fields": ("barcode", "name", "unit", "image", "note"),
                "description": (
                    "Kameradan yoki galereyadan shtrix-kodni o‘qing. "
                    "Bu sahifa faqat sayt admini (staff) uchun."
                ),
            },
        ),
        ("Vaqt", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    class Media:
        css = {"all": ("admin/barcode_catalog.css",)}
        js = (
            "https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js",
            "admin/barcode_catalog.js",
        )
