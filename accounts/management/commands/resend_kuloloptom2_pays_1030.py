"""
kuloloptom-2: 2026-10-30 (kecha) dagi barcha mahsulot to'lovlarini
saytdagi taminotchi guruhiga bir marta qayta yuboradi.

Faqat we_pay va they_pay. Qarzdorlar, kuloloptom va kuloloptomuz ga tegmaydi.
Ikkinchi marta yubormaydi.

  python manage.py resend_kuloloptom2_pays_1030 --send
"""
import html
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from accounts.models import DebtSmsTemplate, SupplierLedger
from accounts.suppliers import _fmt_dt, _fmt_money
from accounts.telethon_debt import parse_group_link, telethon_configured
from accounts.tg_outbox import write_job
from accounts.tg_send_local import send_via_listener

SHOP = "kuloloptom-2"
SENT_FILE = Path(settings.BASE_DIR) / "resend_kuloloptom2_pays_1030.json"


def _day():
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime(2026, 10, 30, 0, 0), tz)
    end = timezone.make_aware(datetime(2026, 10, 31, 0, 0), tz)
    return start, end


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
    res = send_via_listener(job, timeout=25)
    if res.get("ok") or not res.get("offline"):
        return res
    write_job(job)
    return {"ok": False, "queued": True, "error": res.get("error") or "navbatga yozildi"}


def _text(supplier, entry, balance) -> str:
    amt = _fmt_money(abs(entry.amount))
    lines = [
        html.escape(supplier.name, quote=False),
        f"Mahsulot uchun to'lov: {amt} so‘m",
    ]
    try:
        b = Decimal(str(balance or 0))
    except (InvalidOperation, TypeError, ValueError):
        b = Decimal("0")
    if b < 0:
        lines.append(f"Qoldiq: +{_fmt_money(abs(b))} so‘m")
    elif b > 0:
        lines.append(f"Qoldiq (biz qarzdamiz): {_fmt_money(b)} so‘m")
    else:
        lines.append("Qoldiq: 0 so‘m")
    note = (entry.note or "").strip()
    if note:
        lines.append(f"Izoh: {html.escape(note, quote=False)}")
    lines.append(_fmt_dt(entry.created_at))
    return "\n".join(lines)


class Command(BaseCommand):
    help = "kuloloptom-2 kechagi mahsulot to'lovlarini taminotchi guruhiga bir marta yuboradi."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        do_send = bool(options["send"])
        start, end = _day()
        tpl = DebtSmsTemplate.objects.filter(shop_key__iexact=SHOP).first()
        link = ((tpl.supplier_telegram_group_link if tpl else "") or "").strip()
        if link and not parse_group_link(link):
            link = ""
        self.stdout.write(
            f"shop={SHOP} kun=2026-10-30 send={do_send} "
            f"userbot={telethon_configured(SHOP)} "
            f"tolov_guruh={'ha' if link else 'yoq'}"
        )
        qs = (
            SupplierLedger.objects.filter(
                supplier__shop_key__iexact=SHOP,
                created_at__gte=start,
                created_at__lt=end,
                kind__in=(SupplierLedger.KIND_WE_PAY, SupplierLedger.KIND_THEY_PAY),
            )
            .select_related("supplier")
            .order_by("created_at", "id")
        )
        done = _load_sent()
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
            res = _once(
                {
                    "type": "group",
                    "shop": SHOP,
                    "link": link,
                    "text": _text(entry.supplier, entry, bal or 0),
                }
            )
            if res.get("ok") or res.get("queued"):
                done.add(key)
            self.stdout.write(
                "  " + ("ketdi" if res.get("ok") else "xato " + str(res.get("error") or res))
            )
        if do_send:
            _save_sent(done)
        self.stdout.write(f"tayyor mahsulot_tolov={n}")
        if not do_send:
            self.stdout.write("Yuborish: python manage.py resend_kuloloptom2_pays_1030 --send")
