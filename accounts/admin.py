import logging
import traceback

from django.contrib import admin
from django.http import HttpResponse

from .models import (
    DesktopInstaller,
    LabelTemplate,
    ClientDebtor,
    DebtSmsTemplate,
    Supplier,
    SupplierLedger,
    TenantProfile,
)

logger = logging.getLogger("django.request")


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
    """Minimal admin — FileField list_display productionda 500 berishi mumkin."""

    list_display = ("title", "version", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "version")
    readonly_fields = ("created_at", "updated_at")
    fields = ("title", "version", "file", "is_active", "created_at", "updated_at")

    def changelist_view(self, request, extra_context=None):
        try:
            return super().changelist_view(request, extra_context=extra_context)
        except Exception as exc:
            logger.exception("DesktopInstaller changelist failed")
            body = (
                "DesktopInstaller admin xato:\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"{traceback.format_exc()}"
            )
            return HttpResponse(body, status=500, content_type="text/plain; charset=utf-8")

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(
                request, object_id, form_url, extra_context=extra_context
            )
        except Exception as exc:
            logger.exception("DesktopInstaller changeform failed")
            body = (
                "DesktopInstaller forma xato:\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"{traceback.format_exc()}"
            )
            return HttpResponse(body, status=500, content_type="text/plain; charset=utf-8")
