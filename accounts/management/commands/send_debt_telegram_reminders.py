"""
Qarz muddati eslatmalari — Telethon orqali Telegramga.

  python manage.py send_debt_telegram_reminders
  python manage.py send_debt_telegram_reminders --dry-run
  python manage.py send_debt_telegram_reminders --resolve-only
  python manage.py send_debt_telegram_reminders --resolve-only --force
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import ClientDebtor
from accounts.telethon_debt import (
    reminder_kind_for,
    resolve_debtors_batch,
    send_debt_reminders_batch,
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
            help="Faqat Telegram bor-yo‘qligini aniqlash (batch)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Keshni e’tiborsiz qilib qayta resolve",
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
        rows = list(qs.order_by("due_date", "id")[: max(1, options["limit"])])

        if options["resolve_only"]:
            # Faqat hali ok/no_telegram bo‘lmaganlar (yoki --force)
            if options["force"]:
                todo = rows
            else:
                todo = [
                    r
                    for r in rows
                    if r.telegram_status not in ("ok", "no_telegram", "no_phone")
                    or not r.telegram_checked_at
                ]
            self.stdout.write(
                f"Resolve: {len(todo)} ta (batch=8, pauza=4s). Kutish mumkin..."
            )
            resolve_debtors_batch(todo, force=True)
            for row in rows:
                row.refresh_from_db()
                self.stdout.write(
                    f"  {row.name} | {row.phone} | {telegram_display(row)} | {row.telegram_status}"
                )
            ok_n = sum(1 for r in rows if r.telegram_status == "ok")
            no_n = sum(1 for r in rows if r.telegram_status == "no_telegram")
            err_n = sum(1 for r in rows if r.telegram_status == "error")
            self.stdout.write(
                f"Tayyor. ok={ok_n} no_telegram={no_n} error={err_n} total={len(rows)}"
            )
            return

        # Eslatma oldidan keraklilarini resolve
        need_resolve = [
            r
            for r in rows
            if reminder_kind_for(r.due_date, today)
            and r.telegram_status not in ("ok", "no_telegram", "no_phone")
        ]
        if need_resolve:
            self.stdout.write(f"Resolve {len(need_resolve)} ta...")
            resolve_debtors_batch(need_resolve, force=False)
            for r in need_resolve:
                r.refresh_from_db()

        jobs = []
        skipped = 0
        no_tg = 0
        for row in rows:
            kind = reminder_kind_for(row.due_date, today)
            if not kind:
                skipped += 1
                continue
            if row.tg_last_remind_date == today and row.tg_last_remind_kind == kind:
                skipped += 1
                continue
            if row.telegram_status in ("no_telegram", "no_phone"):
                no_tg += 1
                continue
            if row.telegram_status != "ok" or not row.telegram_id:
                no_tg += 1
                continue
            if options["dry_run"]:
                self.stdout.write(
                    f"[dry] {row.name} | {kind} | {row.due_date} | {telegram_display(row)}"
                )
                continue
            jobs.append((row, kind))

        if options["dry_run"]:
            self.stdout.write(
                f"Tayyor. dry_jobs={len(jobs)} skipped={skipped} no_telegram={no_tg}"
            )
            return

        results = send_debt_reminders_batch(jobs) if jobs else []
        sent = sum(1 for r in results if r.get("ok"))
        errors = sum(1 for r in results if not r.get("ok"))
        for r in results:
            if r.get("ok"):
                self.stdout.write(
                    self.style.SUCCESS(f"OK {r.get('name')} → {r.get('display')}")
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f"Xato: {r.get('name')} | {r.get('error')}")
                )

        self.stdout.write(
            f"Tayyor. sent={sent} skipped={skipped} no_telegram={no_tg} errors={errors}"
        )
