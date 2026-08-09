"""Brauzersiz smena → Telegram sync (har 1 daqiqa)."""
from django.core.management.base import BaseCommand

from accounts.models import TenantProfile
from accounts.views import sync_telegram_shifts_for_tenant


class Command(BaseCommand):
    help = "Telegram yoqilgan tenantlar uchun smena ochilish/yopilish xabarlarini yuboradi"

    def handle(self, *args, **options):
        tenants = (
            TenantProfile.objects.filter(telegram_enabled=True)
            .exclude(telegram_bot_token="")
            .exclude(tezpos_api_token="")
            .exclude(tezpos_server_name="")
        )
        total_sent = 0
        for tenant in tenants:
            try:
                result = sync_telegram_shifts_for_tenant(
                    tenant,
                    tenant.tezpos_api_token,
                    tenant.tezpos_server_name,
                )
                n = len(result.get("sent") or [])
                total_sent += n
                self.stdout.write(
                    f"{tenant.business_name}: checked={result.get('checked')} sent={n}"
                )
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"{tenant.business_name}: ERROR {exc}")
        self.stdout.write(self.style.SUCCESS(f"Done. total_sent={total_sent}"))
