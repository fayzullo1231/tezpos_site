"""
Qarzdorlar ro‘yxatini shop_key bo‘yicha import qilish.
Mavjud ismlarni qayta yozmaydi — faqat yo‘qlarini qo‘shadi.

  python manage.py import_debtors_list --shop kuloloptom-2
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import ClientDebtor, ClientDebtorLedger

# name — amount (spaces / nbsp in amount OK). Trailing note stays in name.
RAW_LIST = """
Ravshan aka Askiya bozor  -1 443 000
Abror aka  1 000 000
Xasan Dior market  3 917 000
Avaz aka  2 200 000
Oybek aka Qotgan  1 613 000
Saman Sinf  4 000 000
Aziz aka Madaniyat  2 527 500
Bozor Kenayi  250 000
Isom aka  4 000 000
Jamshid aka mahalla  241 000
Akrom aka  893 000
Islom aka 701  4 405 000
Davron aka Ecco  11 000 000
Shuhrat aka orzu  17 182 000
Hasan Plastik  1 859 000
Nuriddin aka qarindosh  4 800 000
Flesh Astanofka Siroj  11 931 000
Sanjar aka madaniyat  0
Gradent  4 360 000
Olim aka Chig'atoy  5 074 000
Baxti aka Unversam  20 035 100
Ibrat Savdo  1 162 000
Tilla aka Madaniyat  0
Obid aka Unfersam  14 520 000
Abduvohid aka Kokcha  -4 527 800
Murod aka Severiliy  120 172 100
Ismoil aka Navoiy  14 200 000
Abbos aka Kokcha  10 544 400
Anvar aka 5-kvartl  1 485 500
Tolqin aka   -992 600
Muxtor aka   3 000 000
Abdullo aka 9-kvartl  4 300 000
Sardor aka Xozmag  7 485 200
Shag'zod aka Guliston  900 000
Fayzi 9-kvartil  -1 025 000
Flish Halil aka  53 394 000
Sanjar aka Boz bozor  0
Sanjar aka Risivi  0
Qodir aka   2 498 500
Amriddin aka  39 830 400
Diyor Market 1  28 778 000
Diyor Market 2  24 526 200
Barno opa  0
Dilshod aka Tutzor  0
Xasan-Xusan  0
Sanjar Kichkina  0
Barsetka brat  2 362 500
Sanjar aka 555 Sarakulka  5 728 300
Fazliddin tahoe  526 000
Eshon buva  6 892 000
Otabek severny  565 600
Qizil Tut  86 211 260
Shirinboy  2 755 000
Suxrop aka  5 780 000
Nursaid Murod  6 168 000
Nursaid Shoxrux  1 088 000
Apteka  2 760 000
Sanjar aka 555 Beshqozon  2 469 000
Sanjar aka 555 Anor  3 000 000
Sanjar aka 555 Kfc  15 511 000
Expres paynet  2 000 000
Rustam aka Tapouch  0
Avto prom  606 500
Sunnat unchi  152 000
Doston Tepa  152 000
Mahmud unchi  206 000
Siroj aka Pechenni  297 000
Sanjar aka 555 start stadion  0
Muhammad Rasul aka  0
Fazliddin Shohsaroy  1 020 000
Nursaid Jahongir  500
Yashnar aka   3 494 000
Behruz  24 906 500
Karimbek nur  1 745 600
"""

_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<amount>-?[\d\s\u00a0\u202f]+(?:[.,]\d+)?)\s*$"
)


def parse_rows(text: str) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    for raw in text.splitlines():
        line = raw.replace("\u00a0", " ").replace("\u202f", " ").rstrip()
        if not line.strip():
            continue
        # Asosiy: ism va summa orasida 2+ bo‘sh joy
        m = _LINE_RE.match(line)
        if not m:
            # Zaxira: oxirgi raqamli tokenlar
            m2 = re.match(
                r"^(?P<name>.+?)\s+(?P<amount>-?\d(?:[\d\s]*\d)?(?:[.,]\d+)?)\s*$",
                line.strip(),
            )
            if not m2:
                continue
            m = m2
        name = re.sub(r"\s+", " ", m.group("name")).strip()[:180]
        amt_raw = m.group("amount").replace(" ", "").replace(",", ".")
        try:
            amount = Decimal(amt_raw).quantize(Decimal("0.01"))
        except Exception:
            continue
        if name:
            out.append((name, amount))
    return out


class Command(BaseCommand):
    help = "Import hardcoded debtor list into shop_key (skip existing names)."

    def add_arguments(self, parser):
        parser.add_argument("--shop", default="kuloloptom-2")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Faqat hisobla, yozma",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        shop = str(options["shop"]).strip().lower()
        dry = bool(options["dry_run"])
        rows = parse_rows(RAW_LIST)
        existing = {
            n.strip().lower()
            for n in ClientDebtor.objects.filter(shop_key=shop, is_active=True).values_list(
                "name", flat=True
            )
        }
        added = 0
        skipped = 0
        for name, amount in rows:
            key = name.lower()
            if key in existing:
                skipped += 1
                continue
            if dry:
                added += 1
                continue
            row = ClientDebtor.objects.create(
                shop_key=shop,
                name=name,
                phone="",
                note="",
                is_active=True,
            )
            if amount != 0:
                kind = (
                    ClientDebtorLedger.KIND_ADD
                    if amount > 0
                    else ClientDebtorLedger.KIND_SUB
                )
                amt = abs(amount)
                ClientDebtorLedger.objects.create(
                    debtor=row,
                    kind=kind,
                    amount=amt,
                    signed_amount=ClientDebtorLedger.sign_for(kind, amt),
                    note="Boshlang‘ich qarz (import)",
                    created_by="import_debtors_list",
                )
            existing.add(key)
            added += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"shop={shop} total_list={len(rows)} added={added} skipped_existing={skipped} dry={dry}"
            )
        )
