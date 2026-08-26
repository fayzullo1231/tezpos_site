from django.contrib import admin

from .models import (
    DesktopInstaller,
    LabelTemplate,
    ClientDebtor,
    ClientDebtorLedger,
    DebtSmsTemplate,
    Supplier,
    SupplierLedger,
    TenantProfile,
)


@admin.register(TenantProfile)
class TenantProfileAdmin(admin.ModelAdmin):
    list_display = ("business_name", "phone", "telegram_enabled", "created_at")
    search_fields = ("business_name", "phone")


@admin.register(ClientDebtor)
class ClientDebtorAdmin(admin.ModelAdmin):
    list_display = ("name", "shop_key", "phone", "note", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "shop_key", "phone", "note")


@admin.register(DebtSmsTemplate)
class DebtSmsTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "shop_key", "shop_label", "is_approved", "updated_at")
    search_fields = ("title", "shop_key", "shop_label")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "shop_key", "phone", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "shop_key", "phone")


@admin.register(SupplierLedger)
class SupplierLedgerAdmin(admin.ModelAdmin):
    list_display = ("supplier", "kind", "amount", "created_at", "created_by")
    list_filter = ("kind",)
    search_fields = ("supplier__name", "note", "created_by")
    readonly_fields = ("signed_amount",)


@admin.register(LabelTemplate)
class LabelTemplateAdmin(admin.ModelAdmin):
    list_display = ("shop_key", "updated_by", "updated_at")
    search_fields = ("shop_key", "updated_by")
    readonly_fields = ("updated_at",)


@admin.register(DesktopInstaller)
class DesktopInstallerAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "is_active", "file_info", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "version")
    readonly_fields = ("created_at", "updated_at", "file_info")
    fields = (
        "title",
        "version",
        "file",
        "file_info",
        "is_active",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Fayl")
    def file_info(self, obj: DesktopInstaller) -> str:
        try:
            name = (obj.file.name if obj.file else "") or ""
        except Exception:
            return "— (o‘qib bo‘lmadi)"
        if not name:
            return "— yuklanmagan"
        try:
            exists = obj.file.storage.exists(name)
        except Exception:
            exists = False
        short = name.rsplit("/", 1)[-1]
        return f"{short} · {'diskda bor' if exists else 'diskda yo‘q — qayta yuklang'}"

