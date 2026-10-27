"""
kuloloptom taminotchilari + boshlang'ich qarz.

  python manage.py import_suppliers_list --shop kuloloptom --dry-run
  python manage.py import_suppliers_list --shop kuloloptom
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Supplier, SupplierLedger
from accounts.suppliers import _ensure_supplier

RAW_LIST = """
MIKADO  28 331 700
MONARX  33 800 974
TANXO  31 343 200
Lorado  23 707 500
Slaboda  5 825 378
LTZ  0
Musaffo  30 642 763
Brest  186 929 620
Prain Food  0
Milter  24 214 200
Baza Sergeliy  36 300 000
Mari Maloka  57 472 921
Abdulaziz Viola  132 140 000
Shavkat aka Alifka  0
Kallex  105 238 400
Jahon aka Shedrey  27 596 300
Maxeyiv  155 494 298
Buryonka  0
Chimboy mayanez  15 000 000
Chig'atoy mayanez  0
Pinar Labney  17 118 000
Chococrem Shavkat aka  18 503 600
Fetaksa xoxland  107 645 000
Eron krimches  110 892 208
Axror  169 090 513
Maselka  1 540 820
Halol Tushonka  114 178 000
Bon saryog'  53 568 000
Pamiyella  0
Kanatiskiy  92 652 800
Ansor Ibrohim aka   0
Wisks  10 886 400
Ziravor Abduqodir  5 586 000
Activa  0
Bonduel  0
Alsafi  0
Hershis  0
Rim Tushonka  13 990 000
Zarodina  8 791 266
Tukas  0
Mo mo maloko  2 998 800
Ansor Arzon bozor  39 552 000
Ibrohim Fetaksa  65 856 000
Musaffo Tosh oblist  0
Mari 2  0
I Butter saryo  1 680 000
Abdullo chiz  0
Foodmaxx  0
Aroy  0
Doren Malako  0
Barc  0
Asal Sidir  4 597 500
Beltn  0
Euro Food  702 000
Malina Uzb  0
Globus  5 997 200
Abez Fetaksa  57 330 000
"""

# kuloloptom-2. Musbat — biz qarzdamiz. Manfiy — ortiqcha to'lov
# (taminotchi bizdan qarz): balans < 0, ekranda +399 200.
RAW_LIST_K2 = """
Huggies  159 230 140
Tala gel  11 645 000
Tapavuch kenayi  10 158 717
Otabek aka Proktr  44 751 570
Trichap  16 935 000
Baby steep  6 208 000
Eyfil  3 591 500
Zayd  3 590 800
Qudrat aka Carbon  41 156 352
Clear  -399 200
Vanish  3 291 552
Nuriddin aka Palet  73 136 200
Ahror aka O'rizor  65 323 860
Lalaku  -49 867 400
Zafar aka gel  19 716 000
20 80  33 540 990
Kerasys  11 890 295
Colgate  -27 298 611
Tapavuch  116 039 221
Xojakbar aka Lody  11 722 200
Bobir aka 1-40  24 109 995
Kapum  40 894 000
Shag'boz Baza  19 545 750
Rozamari Shanpun  520 000
Sanjar Huggies dellir  0
Eclear  0
Henkil  -262
Nivea  50 934 500
ROCS  0
Siroch Azelit  7 359 000
Clean live  0
Bubchen  0
Grass  29 010 432
Prima  0
Islom Atir  2 400 000
Savannax  0
A+ Firma  8 245 800
Winso  1 884 960
Xitoy Sovun  153 840 000
Salfetka Xitoy  270 000
"""

LISTS = {
    "kuloloptom": RAW_LIST,
    "kuloloptom-2": RAW_LIST_K2,
}

_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<amount>-?[\d\s\u00a0\u202f]+(?:[.,]\d+)?)\s*$"
)


def parse_rows(text: str) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    for raw in text.splitlines():
        line = raw.replace("\u00a0", " ").replace("\u202f", " ").rstrip()
        if not line.strip():
            continue
        if re.fullmatch(r"-?\d{1,3}(?:\s\d{3})+|-?\d+", line.strip()):
            continue
        m = _LINE_RE.match(line)
        if not m:
            m = re.match(
                r"^(?P<name>.+?)\s+(?P<amount>-?\d(?:[\d\s]*\d)?(?:[.,]\d+)?)\s*$",
                line.strip(),
            )
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group("name")).strip()[:180]
        amt_raw = m.group("amount").replace(" ", "").replace(",", ".")
        try:
            amount = Decimal(amt_raw).quantize(Decimal("0.01"))
        except Exception:
            continue
        if name and not name.isdigit():
            out.append((name, amount))
    return out


class Command(BaseCommand):
    help = "Import hardcoded supplier list + opening debt into shop_key."

    def add_arguments(self, parser):
        parser.add_argument("--shop", default="kuloloptom")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        shop = str(options["shop"]).strip().lower()
        dry = bool(options["dry_run"])
        raw = LISTS.get(shop)
        if raw is None:
            self.stderr.write("Ro'yxat yo'q: " + shop)
            return
        rows = parse_rows(raw)
        net = sum((amt for _n, amt in rows), Decimal("0"))
        self.stdout.write(f"{shop}: parsed={len(rows)} net={net}")
        added = 0
        skipped = 0
        debted = 0
        for name, amount in rows:
            row = (
                Supplier.objects.filter(shop_key=shop, name__iexact=name, is_active=True)
                .order_by("id")
                .first()
            )
            if shop == "kuloloptom-2":
                cur = Decimal("0") if row is None else row.balance()
                diff = (amount - cur).quantize(Decimal("0.01"))
                if row is None:
                    if dry:
                        added += 1
                        if diff:
                            debted += 1
                        continue
                    row = _ensure_supplier(shop, name)
                    added += 1
                if diff == 0:
                    skipped += 1
                    continue
                if dry:
                    debted += 1
                    continue
                kind = (
                    SupplierLedger.KIND_WE_OWE
                    if diff > 0
                    else SupplierLedger.KIND_THEY_OWE
                )
                amt = abs(diff)
                SupplierLedger.objects.create(
                    supplier=row,
                    kind=kind,
                    amount=amt,
                    signed_amount=SupplierLedger.sign_for(kind, amt),
                    note="Boshlangich qarz (import)",
                    created_by="import_suppliers_list",
                    created_at=timezone.now(),
                )
                debted += 1
                continue
            if row is None:
                if dry:
                    added += 1
                    if amount:
                        debted += 1
                    continue
                row = _ensure_supplier(shop, name)
                added += 1
            elif dry:
                skipped += 1
                continue
            if amount != 0 and row.balance() == 0:
                if dry:
                    debted += 1
                    continue
                kind = (
                    SupplierLedger.KIND_WE_OWE
                    if amount > 0
                    else SupplierLedger.KIND_THEY_OWE
                )
                amt = abs(amount)
                SupplierLedger.objects.create(
                    supplier=row,
                    kind=kind,
                    amount=amt,
                    signed_amount=SupplierLedger.sign_for(kind, amt),
                    note="Boshlangich qarz (import)",
                    created_by="import_suppliers_list",
                    created_at=timezone.now(),
                )
                debted += 1
            else:
                skipped += 1
        self.stdout.write(
            f"{shop}: rows={len(rows)} added={added} debted={debted} skipped={skipped}"
            + (" DRY" if dry else "")
        )
