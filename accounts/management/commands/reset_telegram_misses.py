"""Noto‘g‘ri «Telegram topilmadi» holatini ochib, qayta qidirishga qo‘yadi.

  python manage.py reset_telegram_misses
  python manage.py reset_telegram_misses --shop kuloloptom
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import ClientDebtor


class Command(BaseCommand):
    help = "False no_telegram/error holatlarini tozalab, listener qayta topadi"

    def add_arguments(self, parser):
        parser.add_argument("--shop", default="", help="Faqat shu do‘kon")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="O‘zgartirmasdan sonini ko‘rsatadi",
        )

    def handle(self, *args, **options):
        shop = str(options.get("shop") or "").strip().lower()
        base = ClientDebtor.objects.filter(is_active=True)
        if shop:
            base = base.filter(shop_key=shop)
        # Avval id borlarni tikla
        healed = (
            base.exclude(telegram_id="")
            .exclude(telegram_status="ok")
            .update(telegram_status="ok")
        )
        qs = base.filter(
            Q(telegram_status="no_telegram") | Q(telegram_status="error") | Q(telegram_status="")
        ).filter(Q(telegram_id="") | Q(telegram_id__isnull=True))
        n = qs.count()
        self.stdout.write(f"Heal (id bor): {healed}; qayta qidirish: {n}")
        if options.get("dry_run"):
            return
        updated = qs.update(telegram_status="", telegram_checked_at=None)
        self.stdout.write(self.style.SUCCESS(f"Tozalandi: {updated}"))
