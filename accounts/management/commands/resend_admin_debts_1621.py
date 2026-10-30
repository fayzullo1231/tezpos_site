"""
Bir marta: 2026-10-30 16:21 dan keyin, admin kiritgan qarzlarni Telegramga yuborish.

Faqat kuloloptom va kuloloptom-2. kuloloptomuz ga tegmaydi.
kuloloptom-2 xabari Kulol Optom userbotidan ketadi.

  python manage.py resend_admin_debts_1621
  python manage.py resend_admin_debts_1621 --send
"""
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from accounts.client_debts import _group_notice_text, debt_group_link
from accounts.models import ClientDebtorLedger, SupplierLedger
from accounts.suppliers import _supplier_group_link, _supplier_notice_text
from accounts.telethon_debt import send_group_message, telethon_configured

SHOPS = ("kuloloptom", "kuloloptom-2")
BLOCKED = "kuloloptomuz"


def _cutoff():
    naive = datetime(2026, 10, 30, 16, 21)
    tz = timezone.get_current_timezone()
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, tz)
    return naive


def _admin_entry(created_by: str, shop: str) -> bool:
    """POS admin sessiyasi created_by ni do‘kon nomi qilib yozadi."""
    who = (created_by or "").strip().lower()
    if not who:
        return False
    if who == "admin" or who.startswith("admin ") or who.endswith(" admin"):
        return True
    if who == shop:
        return True
    return False


class Command(BaseCommand):
    help = "16:21 dan keyin admin kiritgan mijoz va taminotchi qarzlarini bir marta yuboradi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            action="store_true",
            help="Ha yubor. Bo‘lmasa faqat ro‘yxat.",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            "Bu buyruq o'chirilgan. Faqat kuloloptom-2 uchun bir marta:\n"
            "python manage.py resend_kuloloptom2_once --send"
        )
        return
        if BLOCKED in SHOPS:
            self.stderr.write("kuloloptomuz bu buyruqqa kiritilmaydi")
            return
        cutoff = _cutoff()
        do_send = bool(options["send"])
        self.stdout.write(
            f"cutoff {cutoff.isoformat()} shops={','.join(SHOPS)} send={do_send}"
        )
        for shop in SHOPS:
            ok = telethon_configured(shop)
            self.stdout.write(
                f"userbot shop={shop} configured={ok} "
                f"mijoz_guruh={'ha' if debt_group_link(shop) else 'yoq'} "
                f"taminotchi_guruh={'ha' if _supplier_group_link(shop) else 'yoq'}"
            )

        client_n = self._clients(cutoff, do_send)
        supplier_n = self._suppliers(cutoff, do_send)
        self.stdout.write(f"tayyor mijoz={client_n} taminotchi={supplier_n}")
        if not do_send:
            self.stdout.write("Yuborish uchun: python manage.py resend_admin_debts_1621 --send")

    def _clients(self, cutoff, do_send: bool) -> int:
        qs = (
            ClientDebtorLedger.objects.filter(
                debtor__shop_key__in=SHOPS,
                created_at__gt=cutoff,
            )
            .select_related("debtor")
            .order_by("created_at", "id")
        )
        n = 0
        for entry in qs:
            shop = (entry.debtor.shop_key or "").strip().lower()
            if shop == BLOCKED or shop not in SHOPS:
                continue
            if not _admin_entry(entry.created_by, shop):
                self.stdout.write(
                    f"SKIP mijoz created_by={entry.created_by or '-'} shop={shop} id={entry.pk}"
                )
                continue
            bal = entry.debtor.ledger.filter(id__lte=entry.pk).aggregate(
                s=Sum("signed_amount")
            )["s"]
            text = _group_notice_text(entry.debtor, entry, bal)
            link = debt_group_link(shop)
            self.stdout.write(
                f"MIJOZ shop={shop} {entry.created_at:%H:%M} {entry.debtor.name} "
                f"{entry.kind} {entry.amount} by={entry.created_by or '-'}"
            )
            n += 1
            if not do_send:
                continue
            if not link:
                self.stdout.write(f"  guruh havolasi yo‘q shop={shop}")
                continue
            res = send_group_message(link, text, shop_key=shop)
            self.stdout.write(
                "  " + ("ketdi" if res.get("ok") else "xato " + str(res.get("error") or res))
            )
        return n

    def _suppliers(self, cutoff, do_send: bool) -> int:
        qs = (
            SupplierLedger.objects.filter(
                supplier__shop_key__in=SHOPS,
                created_at__gt=cutoff,
            )
            .select_related("supplier")
            .order_by("created_at", "id")
        )
        labels = dict(SupplierLedger.KIND_CHOICES)
        n = 0
        for entry in qs:
            shop = (entry.supplier.shop_key or "").strip().lower()
            if shop == BLOCKED or shop not in SHOPS:
                continue
            if not _admin_entry(entry.created_by, shop):
                self.stdout.write(
                    f"SKIP taminotchi created_by={entry.created_by or '-'} shop={shop} id={entry.pk}"
                )
                continue
            bal = entry.supplier.ledger.filter(id__lte=entry.pk).aggregate(
                s=Sum("signed_amount")
            )["s"]
            kind = entry.kind
            text = _supplier_notice_text(
                entry.supplier,
                payment_label=labels.get(kind, kind),
                amount=entry.amount,
                balance=bal or 0,
                note=entry.note or "",
                is_debt=kind in (SupplierLedger.KIND_WE_OWE, SupplierLedger.KIND_THEY_PAY),
            )
            link = _supplier_group_link(shop)
            self.stdout.write(
                f"TAMINOTCHI shop={shop} {entry.created_at:%H:%M} {entry.supplier.name} "
                f"{kind} {entry.amount} by={entry.created_by or '-'}"
            )
            n += 1
            if not do_send:
                continue
            if not link:
                self.stdout.write(f"  taminotchi guruh havolasi yo‘q shop={shop}")
                continue
            res = send_group_message(link, text, shop_key=shop)
            self.stdout.write(
                "  " + ("ketdi" if res.get("ok") else "xato " + str(res.get("error") or res))
            )
        return n
