"""
kuloloptom-2: 2026-10-30 16:21 dan keyingi yozuvlarni saytdagi guruhga bir marta.

- Qarzdorlar: qo'shilgan, ayirilgan, to'lov — mijoz guruhiga
- Mahsulot uchun to'lov — taminotchi guruhiga
kuloloptom va kuloloptomuz ga tegmaydi. Ikkinchi marta yubormaydi.

  python manage.py resend_kuloloptom2_once --send
"""
import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from accounts.client_debts import _group_notice_text
from accounts.models import ClientDebtorLedger, DebtSmsTemplate, SupplierLedger
from accounts.suppliers import _supplier_notice_text
from accounts.telethon_debt import parse_group_link, telethon_configured
from accounts.tg_outbox import write_job
from accounts.tg_send_local import send_via_listener

SHOP = "kuloloptom-2"
SENT_FILE = Path(settings.BASE_DIR) / "resend_kuloloptom2_once.json"


def _cutoff():
    naive = datetime(2026, 10, 30, 16, 21)
    tz = timezone.get_current_timezone()
    return timezone.make_aware(naive, tz) if timezone.is_naive(naive) else naive


def _load_sent() -> set[str]:
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def _save_sent(done: set[str]) -> None:
    SENT_FILE.write_text(
        json.dumps(sorted(done), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _once(job: dict) -> dict:
    """Listenerga bir marta. U o'chiq bo'lsa navbatga bir marta. Ikkalasi emas."""
    res = send_via_listener(job, timeout=25)
    if res.get("ok") or not res.get("offline"):
        return res
    write_job(job)
    return {"ok": False, "queued": True, "error": res.get("error") or "navbatga yozildi"}


class Command(BaseCommand):
    help = "kuloloptom-2 qarz va mahsulot to'lovlarini sayt guruhiga bir marta yuboradi."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        do_send = bool(options["send"])
        cutoff = _cutoff()
        tpl = DebtSmsTemplate.objects.filter(shop_key__iexact=SHOP).first()
        client_link = ((tpl.telegram_group_link if tpl else "") or "").strip()
        supplier_link = ((tpl.supplier_telegram_group_link if tpl else "") or "").strip()
        if client_link and not parse_group_link(client_link):
            client_link = ""
        if supplier_link and not parse_group_link(supplier_link):
            supplier_link = ""
        self.stdout.write(
            f"shop={SHOP} cutoff={cutoff.isoformat()} send={do_send} "
            f"userbot={telethon_configured(SHOP)} "
            f"qarzdor_guruh={'ha' if client_link else 'yoq'} "
            f"tolov_guruh={'ha' if supplier_link else 'yoq'}"
        )
        done = _load_sent()
        n1 = self._clients(cutoff, do_send, client_link, done)
        n2 = self._pays(cutoff, do_send, supplier_link, done)
        if do_send:
            _save_sent(done)
        self.stdout.write(f"tayyor qarzdor={n1} mahsulot_tolov={n2}")
        if not do_send:
            self.stdout.write("Yuborish: python manage.py resend_kuloloptom2_once --send")

    def _clients(self, cutoff, do_send, link, done: set[str]) -> int:
        qs = (
            ClientDebtorLedger.objects.filter(
                debtor__shop_key__iexact=SHOP,
                created_at__gt=cutoff,
            )
            .select_related("debtor")
            .order_by("created_at", "id")
        )
        n = 0
        for entry in qs:
            key = f"mijoz:{entry.pk}"
            bal = entry.debtor.ledger.filter(id__lte=entry.pk).aggregate(
                s=Sum("signed_amount")
            )["s"]
            self.stdout.write(
                f"QARZDOR {entry.created_at:%H:%M} {entry.debtor.name} "
                f"{entry.kind} {entry.amount}"
                + (" allaqachon" if key in done else "")
            )
            n += 1
            if not do_send or key in done:
                continue
            if not link:
                self.stdout.write("  saytda qarzdorlar guruhi yo'q")
                continue
            text = _group_notice_text(entry.debtor, entry, bal)
            res = _once(
                {"type": "group", "shop": SHOP, "link": link, "text": text}
            )
            if res.get("ok") or res.get("queued"):
                done.add(key)
            self.stdout.write(
                "  " + ("ketdi" if res.get("ok") else "xato " + str(res.get("error") or res))
            )
        return n

    def _pays(self, cutoff, do_send, link, done: set[str]) -> int:
        qs = (
            SupplierLedger.objects.filter(
                supplier__shop_key__iexact=SHOP,
                created_at__gt=cutoff,
                kind__in=(SupplierLedger.KIND_WE_PAY, SupplierLedger.KIND_THEY_PAY),
            )
            .select_related("supplier")
            .order_by("created_at", "id")
        )
        labels = dict(SupplierLedger.KIND_CHOICES)
        n = 0
        for entry in qs:
            key = f"tolov:{entry.pk}"
            bal = entry.supplier.ledger.filter(id__lte=entry.pk).aggregate(
                s=Sum("signed_amount")
            )["s"]
            self.stdout.write(
                f"TOLOV {entry.created_at:%H:%M} {entry.supplier.name} "
                f"{entry.kind} {entry.amount}"
                + (" allaqachon" if key in done else "")
            )
            n += 1
            if not do_send or key in done:
                continue
            if not link:
                self.stdout.write("  saytda taminotchi guruhi yo'q")
                continue
            text = _supplier_notice_text(
                entry.supplier,
                payment_label="Mahsulot uchun to'lov",
                amount=entry.amount,
                balance=bal or 0,
                note=entry.note or labels.get(entry.kind, ""),
                is_debt=False,
            )
            res = _once(
                {"type": "group", "shop": SHOP, "link": link, "text": text}
            )
            if res.get("ok") or res.get("queued"):
                done.add(key)
            self.stdout.write(
                "  " + ("ketdi" if res.get("ok") else "xato " + str(res.get("error") or res))
            )
        return n
