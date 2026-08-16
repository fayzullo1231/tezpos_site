"""Brauzersiz smena → Telegram sync. Bir vaqtda faqat bitta process."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import TenantProfile
from accounts.views import sync_telegram_shifts_for_tenant

logger = logging.getLogger("tezpos.telegram")

LOCK_PATH = Path(settings.BASE_DIR) / "telegram_sync.lock"


class Command(BaseCommand):
    help = "Telegram yoqilgan tenantlar uchun smena ochilish/yopilish xabarlarini yuboradi"

    def handle(self, *args, **options):
        lock_fh = open(LOCK_PATH, "a+", encoding="utf-8")
        try:
            if not self._try_lock(lock_fh):
                self.stdout.write("skip: another sync_telegram_shifts is running")
                logger.info("telegram sync skipped (lock held)")
                return
            t0 = time.time()
            tenants = (
                TenantProfile.objects.filter(telegram_enabled=True)
                .exclude(telegram_bot_token="")
                .exclude(tezpos_api_token="")
                .exclude(tezpos_server_name="")
            )
            total_sent = 0
            errors = 0
            for tenant in tenants:
                try:
                    result = sync_telegram_shifts_for_tenant(
                        tenant,
                        tenant.tezpos_api_token,
                        tenant.tezpos_server_name,
                    )
                    n = len(result.get("sent") or [])
                    total_sent += n
                    line = (
                        f"{tenant.business_name}: checked={result.get('checked')} "
                        f"sent={n} duration={result.get('duration_s')}s "
                        f"api={result.get('api_s')}s db={result.get('db_s')}s"
                    )
                    self.stdout.write(line)
                    logger.info("telegram sync %s", line)
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    logger.exception("telegram sync failed for %s", tenant.business_name)
                    self.stderr.write(f"{tenant.business_name}: ERROR {exc}")
            dt = time.time() - t0
            self.stdout.write(self.style.SUCCESS(
                f"Done. total_sent={total_sent} errors={errors} duration={dt:.2f}s"
            ))
            logger.info(
                "telegram sync finished sent=%s errors=%s duration=%.2fs",
                total_sent, errors, dt,
            )
        finally:
            self._unlock(lock_fh)
            lock_fh.close()

    @staticmethod
    def _try_lock(fh) -> bool:
        try:
            import fcntl
        except ImportError:
            return True
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    @staticmethod
    def _unlock(fh) -> None:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
