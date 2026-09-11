"""
Qarz muddati eslatmalari — Telethon orqali Telegramga.

Har kuni:
  - 1 kun oldin (soon)
  - bugun (due_today)
  - muddati o'tgan (overdue)

Ishga tushirish:
  python manage.py send_debt_telegram_reminders
  python manage.py send_debt_telegram_reminders --dry-run
  python manage.py send_debt_telegram_reminders --resolve-only
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import ClientDebtor
from accounts.telethon_debt import (
    ensure_telegram_resolved,
    reminder_kind_for,
    send_debt_reminder,
    telegram_display,
    telethon_configured,
)


class Command(BaseCommand):
    help = "Qarz muddati Telegram eslatmalarini yuboradi (Telethon)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--resolve-only",
            action="store_true",
            help="Faqat Telegram bor-yo‘qligini aniqlash",
        )
        parser.add_argument("--shop", default="", help="Faqat shu shop_key")
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        if not telethon_configured():
            self.stderr.write(
                self.style.ERROR(
                    "Telethon sozlanmagan. .env ga TELETHON_API_ID, "
                    "TELETHON_API_HASH, TELETHON_SESSION yozing."
                )
            )
            return

        today = timezone.localdate()
        qs = (
            ClientDebtor.objects.filter(is_active=True)
            .annotate(
                bal=Coalesce(
                    Sum("ledger__signed_amount"),
                    Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
                )
            )
            .filter(bal__gt=0)
            .exclude(phone="")
        )
        shop = (options.get("shop") or "").strip()
        if shop:
            qs = qs.filter(shop_key=shop)
        qs = qs.order_by("due_date", "id")[: max(1, options["limit"])]

        sent = 0
        skipped = 0
        no_tg = 0
        errors = 0

        for row in qs:
            if options["resolve_only"]:
                ensure_telegram_resolved(row, force=True)
                self.stdout.write(
                    f"  {row.name} | {row.phone} | {telegram_display(row)} | {row.telegram_status}"
                )
                continue

            kind = reminder_kind_for(row.due_date, today)
            if not kind:
                skipped += 1
                continue

            # Bir kunda bir xil turdagi eslatmani qayta yubormaslik
            if (
                row.tg_last_remind_date == today
                and row.tg_last_remind_kind == kind
            ):
                skipped += 1
                continue

            if options["dry_run"]:
                self.stdout.write(
                    f"[dry] {row.name} | {kind} | {row.due_date} | bal={row.bal}"
                )
                sent += 1
                continue

            res = send_debt_reminder(row, kind)
            if res.get("ok"):
                sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK {row.name} → {res.get('display')} ({kind})"
                    )
                )
            elif res.get("status") in ("no_telegram", "no_phone"):
                no_tg += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"TG yo‘q: {row.name} | {telegram_display(row)}"
                    )
                )
            else:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"Xato: {row.name} | {res.get('error')}"
                    )
                )

        self.stdout.write(
            f"Tayyor. sent={sent} skipped={skipped} no_telegram={no_tg} errors={errors}"
        )
