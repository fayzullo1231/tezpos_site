from django.contrib import admin

from .models import DesktopInstaller, LabelTemplate, TenantProfile


@admin.register(TenantProfile)
class TenantProfileAdmin(admin.ModelAdmin):
    list_display = ("business_name", "phone", "telegram_enabled", "created_at")
    search_fields = ("business_name", "phone")


@admin.register(LabelTemplate)
class LabelTemplateAdmin(admin.ModelAdmin):
    list_display = ("shop_key", "updated_by", "updated_at")
    search_fields = ("shop_key", "updated_by")
    readonly_fields = ("updated_at",)


@admin.register(DesktopInstaller)
class DesktopInstallerAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "is_active", "file", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "version")
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "title",
        "version",
        "file",
        "is_active",
        "created_at",
        "updated_at",
    )
