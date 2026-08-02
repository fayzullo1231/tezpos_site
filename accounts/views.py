"""Shaxsiy kabinet — TezPOS backend ma'lumotlari."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean, pstdev
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import tezpos_api
from .auth_views import (
    SESSION_DISPLAY,
    SESSION_SERVER,
    SESSION_TOKEN,
    clear_tezpos_session,
    session_has_tezpos,
)
from .models import TenantProfile


PAYMENT_LABELS = {
    "cash": "Naqt",
    "card": "Karta",
    "mixed": "Aralash",
    "credit": "Qarzga",
    "click": "Click",
    "payme": "Payme",
    "transfer": "O‘tkazma",
}


def get_tenant_for_user(user):
    tenant, _ = TenantProfile.objects.get_or_create(
        user=user, defaults={"business_name": user.get_full_name() or user.username}
    )
    return tenant


def _dec(value, default="0") -> Decimal:
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value).replace(",", ".").replace(" ", "").replace("\u00a0", ""))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _parse_sale_date(raw):
    if not raw:
        return timezone.localdate()
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return timezone.localdate()


def _cashier_name(request) -> str:
    display = (request.session.get(SESSION_DISPLAY) or "").strip()
    if display:
        return display
    user = request.user
    return (user.get_full_name() or "").strip() or user.username or "Kassir"


def _payment_label(method):
    return PAYMENT_LABELS.get((method or "").lower(), method or "Naqt")


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    dt = parse_datetime(str(raw).replace("Z", "+00:00"))
    if dt is None:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _sale_day(sale: dict) -> date | None:
    dt = _parse_dt(sale.get("completed_at") or sale.get("created_at"))
    if not dt:
        return None
    return timezone.localtime(dt).date()


def _rel_image_url(url: str | None) -> str:
    if not url:
        return ""
    url = str(url)
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = tezpos_api.normalize_api_base()
    if url.startswith("/"):
        return f"{base}{url}"
    return f"{base}/{url}"


def _map_product(raw: dict) -> SimpleNamespace:
    codes = list(raw.get("barcodes") or [])
    barcode = (raw.get("barcode") or "").strip()
    if barcode and barcode not in codes:
        codes.insert(0, barcode)

    list_prices = raw.get("list_prices") or {}
    wholesale = Decimal("0")
    if isinstance(list_prices, dict) and list_prices:
        try:
            wholesale = min(_dec(v) for v in list_prices.values())
        except ValueError:
            wholesale = Decimal("0")

    image_url = _rel_image_url(raw.get("image_url") or raw.get("image"))
    images_raw = raw.get("images") or []
    images = []
    for img in images_raw:
        if not isinstance(img, dict):
            continue
        images.append(
            SimpleNamespace(
                id=img.get("id"),
                image=SimpleNamespace(url=_rel_image_url(img.get("url"))),
                is_primary=bool(img.get("is_primary")),
            )
        )

    stock = _dec(raw.get("quantity"))
    selling = _dec(raw.get("price"))
    cost = _dec(raw.get("cost_price"))
    min_stock = _dec(raw.get("min_stock"), "0")
    category = (raw.get("category_name") or "").strip()
    brand = (raw.get("brand_name") or "").strip()
    name = (raw.get("name") or "").strip() or "Mahsulot"
    pid = str(raw.get("id") or "")
    if cost > 0:
        margin_pct = float((selling - cost) / cost * 100)
    else:
        margin_pct = 0.0

    p = SimpleNamespace(
        id=pid,
        name=name,
        sku=(raw.get("sku") or "").strip(),
        barcode=barcode or (codes[0] if codes else ""),
        barcodes=codes,
        barcode_list=codes,
        unit=(raw.get("unit") or "dona").strip() or "dona",
        category=category,
        brand=brand,
        selling_price=selling,
        wholesale_price=wholesale,
        cost_price=cost,
        stock_qty=stock,
        min_stock=min_stock,
        margin_pct=margin_pct,
        is_favorite=bool(raw.get("is_favorite")),
        is_active=bool(raw.get("is_active", True)),
        image_url=image_url,
        display_image=image_url,
        is_low_stock=stock <= min_stock,
        images=SimpleNamespace(
            all=lambda: images,
            exists=lambda: bool(images),
            filter=lambda **kw: SimpleNamespace(
                first=lambda: next(
                    (i for i in images if all(getattr(i, k) == v for k, v in kw.items())),
                    None,
                )
            ),
            first=lambda: images[0] if images else None,
        ),
        _raw=raw,
    )
    return p


def _empty_form(errors=None):
    return SimpleNamespace(errors=errors or {})


def _build_charts_from_sales(sales: list[dict], today: date):
    def bucket_daily(start: date, days: int, fmt="%d.%m"):
        totals_map = defaultdict(float)
        counts_map = defaultdict(int)
        for s in sales:
            d = _sale_day(s)
            if not d or d < start or d > start + timedelta(days=days - 1):
                continue
            totals_map[d] += float(_dec(s.get("total")))
            counts_map[d] += 1
        labels, totals, counts = [], [], []
        for i in range(days):
            day = start + timedelta(days=i)
            labels.append(day.strftime(fmt))
            totals.append(totals_map.get(day, 0.0))
            counts.append(counts_map.get(day, 0))
        return {"labels": labels, "totals": totals, "counts": counts}

    def hourly_today():
        totals_map = defaultdict(float)
        counts_map = defaultdict(int)
        for s in sales:
            if _sale_day(s) != today:
                continue
            dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
            if not dt:
                continue
            hour = timezone.localtime(dt).hour
            totals_map[hour] += float(_dec(s.get("total")))
            counts_map[hour] += 1
        labels, totals, counts = [], [], []
        for hour in range(24):
            labels.append(f"{hour:02d}:00")
            totals.append(totals_map.get(hour, 0.0))
            counts.append(counts_map.get(hour, 0))
        return {"labels": labels, "totals": totals, "counts": counts}

    def monthly(months: int):
        y, m = today.year, today.month
        starts = []
        for _ in range(months):
            starts.append(date(y, m, 1))
            m -= 1
            if m <= 0:
                m = 12
                y -= 1
        starts.reverse()
        totals_map = defaultdict(float)
        counts_map = defaultdict(int)
        for s in sales:
            d = _sale_day(s)
            if not d:
                continue
            key = date(d.year, d.month, 1)
            totals_map[key] += float(_dec(s.get("total")))
            counts_map[key] += 1
        labels, totals, counts = [], [], []
        for start in starts:
            labels.append(start.strftime("%m.%Y"))
            totals.append(totals_map.get(start, 0.0))
            counts.append(counts_map.get(start, 0))
        return {"labels": labels, "totals": totals, "counts": counts}

    def weekly(start: date, weeks: int):
        totals_map = defaultdict(float)
        counts_map = defaultdict(int)
        for s in sales:
            d = _sale_day(s)
            if not d or d < start:
                continue
            week_start = d - timedelta(days=d.weekday())
            totals_map[week_start] += float(_dec(s.get("total")))
            counts_map[week_start] += 1
        labels, totals, counts = [], [], []
        cursor = start - timedelta(days=start.weekday())
        for i in range(weeks):
            key = cursor + timedelta(weeks=i)
            labels.append(key.strftime("%d.%m"))
            totals.append(totals_map.get(key, 0.0))
            counts.append(counts_map.get(key, 0))
        return {"labels": labels, "totals": totals, "counts": counts}

    month_start = today.replace(day=1)
    sales_stats = {
        "d1": hourly_today(),
        "d7": bucket_daily(today - timedelta(days=6), 7),
        "d15": bucket_daily(today - timedelta(days=14), 15),
        "d30": bucket_daily(today - timedelta(days=29), 30),
        "m1": bucket_daily(month_start, max((today - month_start).days + 1, 1)),
        "m3": weekly(today - timedelta(weeks=12), 13),
        "m6": monthly(6),
        "y1": monthly(12),
    }
    return sales_stats


def _fetch_sale_details(token: str, server: str, sale_ids: list[str], limit: int = 40) -> dict[str, dict]:
    """Fetch sale receipts; never raises — timeouts/errors skip that sale."""
    out: dict[str, dict] = {}
    ids = [str(x) for x in sale_ids[:limit] if x]

    def one(sid: str):
        try:
            return sid, tezpos_api.get_sale(token, server, sid)
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            return sid, None
        except Exception:
            return sid, None

    if not ids:
        return out
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, sid) for sid in ids]
        for fut in as_completed(futures):
            try:
                sid, data = fut.result()
            except Exception:
                continue
            if data:
                out[sid] = data
    return out


def _estimate_sale_cost(sale_detail: dict, products_by_id: dict[str, SimpleNamespace]) -> Decimal:
    cost = Decimal("0")
    for item in sale_detail.get("items") or []:
        qty = _dec(item.get("quantity"))
        pid = str(item.get("product_id") or "")
        p = products_by_id.get(pid)
        unit_cost = p.cost_price if p else Decimal("0")
        cost += qty * unit_cost
    return cost


def _serialize_sale_payload(sale_detail: dict, cashier: str, products_by_id: dict) -> dict:
    items = []
    items_cost = Decimal("0")
    for item in sale_detail.get("items") or []:
        qty = _dec(item.get("quantity"))
        unit_price = _dec(item.get("unit_price"))
        pid = str(item.get("product_id") or "")
        p = products_by_id.get(pid)
        unit_cost = p.cost_price if p else Decimal("0")
        line_total = _dec(item.get("total"), str(qty * unit_price))
        line_cost = qty * unit_cost
        items_cost += line_cost
        items.append(
            {
                "name": item.get("product_name") or (p.name if p else "Mahsulot"),
                "qty": float(qty),
                "unit_price": float(unit_price),
                "unit_cost": float(unit_cost),
                "line_total": float(line_total),
                "line_cost": float(line_cost),
            }
        )
    total_amount = _dec(sale_detail.get("total"))
    profit = total_amount - items_cost
    dt = _parse_dt(sale_detail.get("completed_at") or sale_detail.get("created_at"))
    created_display = timezone.localtime(dt).strftime("%d.%m.%Y, %H:%M") if dt else ""
    method = sale_detail.get("payment_type") or "cash"
    return {
        "id": str(sale_detail.get("id") or ""),
        "created_at": dt.isoformat() if dt else "",
        "created_display": created_display,
        "customer": sale_detail.get("customer_name") or "",
        "cashier": cashier,
        "status": "Yakunlangan",
        "type": "Sotilgan",
        "payment_method": method,
        "payment_label": _payment_label(method),
        "total_amount": float(total_amount),
        "total_cost": float(items_cost),
        "profit": float(profit),
        "discount": float(_dec(sale_detail.get("discount_amount"))),
        "items": items,
    }


def _build_abc_xyz(products, item_rows, today: date):
    start = today - timedelta(days=29)
    revenue = defaultdict(Decimal)
    daily_qty = defaultdict(lambda: defaultdict(float))
    for row in item_rows:
        pid = str(row.get("product_id") or "")
        if not pid:
            continue
        qty = _dec(row.get("quantity"))
        unit_price = _dec(row.get("unit_price"))
        revenue[pid] += qty * unit_price
        day = row.get("day")
        if isinstance(day, date):
            daily_qty[pid][day] += float(qty)

    total_rev = sum(revenue.values()) or Decimal("1")
    ranked = sorted(revenue.items(), key=lambda x: x[1], reverse=True)
    abc_map = {}
    cumulative = Decimal("0")
    for pid, rev in ranked:
        cumulative += rev
        share = cumulative / total_rev * 100
        if share <= 80:
            abc_map[pid] = "A"
        elif share <= 95:
            abc_map[pid] = "B"
        else:
            abc_map[pid] = "C"
    for p in products:
        abc_map.setdefault(str(p.id), "C")

    xyz_map = {}
    for p in products:
        pid = str(p.id)
        series = [daily_qty[pid].get(start + timedelta(days=i), 0.0) for i in range(30)]
        if sum(series) <= 0:
            xyz_map[pid] = "Z"
            continue
        avg = mean(series)
        if avg <= 0:
            xyz_map[pid] = "Z"
            continue
        cv = pstdev(series) / avg if len(series) > 1 else 0
        if cv < 0.5:
            xyz_map[pid] = "X"
        elif cv < 1.0:
            xyz_map[pid] = "Y"
        else:
            xyz_map[pid] = "Z"

    matrix = {f"{a}{x}": [] for a in "ABC" for x in "XYZ"}
    rows = []
    for p in products:
        pid = str(p.id)
        group = f"{abc_map[pid]}{xyz_map[pid]}"
        rev = revenue.get(pid, Decimal("0"))
        entry = {
            "product": p,
            "abc": abc_map[pid],
            "xyz": xyz_map[pid],
            "group": group,
            "revenue": rev,
            "share": float(rev / total_rev * 100),
        }
        matrix[group].append(entry)
        rows.append(entry)
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    matrix_counts = {k: len(v) for k, v in matrix.items()}
    return rows, matrix_counts, float(total_rev)


def _build_near_min_stock(products):
    rows = []
    for p in products:
        min_qty = p.min_stock if p.min_stock is not None else Decimal("0")
        if p.stock_qty <= min_qty:
            rows.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "stock": float(p.stock_qty),
                    "min_stock": float(min_qty),
                    "title": "Kam qoldiq",
                    "text": f"{p.name} — qoldiq {p.stock_qty}",
                    "channels": ["SMS", "Bildirishnoma"],
                    "level": "warn" if p.stock_qty > 0 else "danger",
                }
            )
    rows.sort(key=lambda r: (r["stock"] / r["min_stock"] if r["min_stock"] else 0, r["stock"]))
    return rows


def _export_daily_sales_excel(day_sales_payload, sale_date, cashier):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"kunlik_sotuv_{sale_date.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Sana",
            "Kassir",
            "Mijoz",
            "Turi",
            "Status",
            "To‘lov",
            "Jami",
            "Umumiy tannarxi",
            "Foyda",
            "Mahsulotlar",
        ]
    )
    for payload in day_sales_payload:
        products_txt = " | ".join(
            f"{it['name']} ({it['qty']:g} x {it['unit_price']:g})" for it in payload["items"]
        )
        writer.writerow(
            [
                f"#{payload['id']}",
                payload.get("created_display") or "",
                cashier,
                payload["customer"],
                payload["type"],
                payload["status"],
                payload["payment_label"],
                f'{payload["total_amount"]:.2f}'.replace(".", ","),
                f'{payload["total_cost"]:.2f}'.replace(".", ","),
                f'{payload["profit"]:.2f}'.replace(".", ","),
                products_txt,
            ]
        )
    return response


def _save_product_via_api(request, token: str, server: str, product_id: str | None):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return False, {"name": ["Mahsulot nomi majburiy."]}

    codes = [c.strip() for c in request.POST.getlist("barcodes") if c.strip()]
    barcode = codes[0] if codes else (request.POST.get("barcode") or "").strip()
    payload = {
        "name": name,
        "unit": (request.POST.get("unit") or "dona").strip() or "dona",
        "price": str(_dec(request.POST.get("selling_price"))),
        "cost_price": str(_dec(request.POST.get("cost_price"))),
        "barcode": barcode,
        "barcodes": codes or ([barcode] if barcode else []),
        "is_active": True,
    }
    try:
        if product_id:
            tezpos_api.update_product(token, server, product_id, payload)
        else:
            tezpos_api.create_product(token, server, payload)
        return True, {}
    except tezpos_api.TezPosApiError as exc:
        return False, {"__all__": [str(exc)]}


@login_required
def cabinet_view(request):
    if not session_has_tezpos(request):
        clear_tezpos_session(request)
        messages.warning(request, "TezPOS hisobiga qayta kiring.")
        return redirect("login")

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    tenant = get_tenant_for_user(request.user)
    display = request.session.get(SESSION_DISPLAY) or tenant.business_name
    if display and tenant.business_name != display:
        tenant.business_name = display
        tenant.save(update_fields=["business_name"])

    section = request.GET.get("section", "overview")
    sale_date = _parse_sale_date(request.GET.get("sale_date"))
    form = _empty_form()
    section_force = None

    if request.method == "POST":
        product_id = (request.POST.get("product_id") or "").strip() or None
        ok, errors = _save_product_via_api(request, token, server, product_id)
        if ok:
            messages.success(request, "Mahsulot TezPOS ga saqlandi.")
            return redirect(f"{request.path}?section=products")
        form = _empty_form(errors)
        section_force = "products"

    api_warnings = []
    today = timezone.localdate()
    raw_products: list = []
    raw_sales: list = []
    day_sales_raw: list = []

    section = section_force or request.GET.get("section", "overview")
    need_products = True  # ko'p bo'limlarda kerak (signallar, ABC, ...)
    need_chart_sales = section in ("overview", "reports", "tops", "abc", "sales")
    need_day_sales = section in ("overview", "sales")
    # Har bir chek detaili juda sekin — ro'yxat yetarli; tannarx keyinroq
    need_sale_details = False
    need_top_stats = section in ("tops", "abc")

    def _load_products():
        return tezpos_api.get_products(token, server)

    def _load_chart_sales():
        last_err = None
        # Avval 14 kun (tez), kerak bo'lsa kengaytiriladi
        for days in (14, 7, 30):
            try:
                return tezpos_api.get_sales(
                    token,
                    server,
                    date_from=(today - timedelta(days=days)).isoformat(),
                    date_to=today.isoformat(),
                    timeout=40,
                )
            except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
                last_err = exc
                if getattr(exc, "status", None) in (401, 403):
                    raise
        if last_err:
            raise last_err
        return []

    def _auth_fail(exc):
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            messages.error(request, "Sessiya tugadi. Qayta kiring.")
            return True
        return False

    # Mahsulotlar (+ ixtiyoriy grafik sotuvlari) parallel
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_p = pool.submit(_load_products) if need_products else None
            fut_s = pool.submit(_load_chart_sales) if need_chart_sales else None
            if fut_p is not None:
                try:
                    raw_products = fut_p.result()
                except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
                    if _auth_fail(exc):
                        return redirect("login")
                    api_warnings.append(f"Mahsulotlar: {exc}")
                    raw_products = []
            if fut_s is not None:
                try:
                    raw_sales = fut_s.result()
                except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
                    if _auth_fail(exc):
                        return redirect("login")
                    api_warnings.append(f"Sotuvlar: {exc}")
                    raw_sales = []
    except Exception as exc:
        api_warnings.append(str(exc))

    if need_day_sales:
        try:
            day_sales_raw = tezpos_api.get_sales_for_day(
                token, server, sale_date.isoformat()
            )
        except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
            if _auth_fail(exc):
                return redirect("login")
            day_sales_raw = [
                s for s in raw_sales if isinstance(s, dict) and _sale_day(s) == sale_date
            ]

        # Bugun sotuv yo'q — oxirgi sotuv kuniga (faqat default sana)
        if (
            not day_sales_raw
            and not request.GET.get("sale_date")
            and section in ("overview", "sales")
        ):
            latest = None
            for s in raw_sales:
                if not isinstance(s, dict):
                    continue
                d = _sale_day(s)
                if d and (latest is None or d > latest):
                    latest = d
            if latest and latest != sale_date:
                sale_date = latest
                try:
                    day_sales_raw = tezpos_api.get_sales_for_day(
                        token, server, sale_date.isoformat()
                    )
                except (tezpos_api.TezPosApiError, TimeoutError, OSError):
                    day_sales_raw = [
                        s
                        for s in raw_sales
                        if isinstance(s, dict) and _sale_day(s) == sale_date
                    ]

    if api_warnings:
        messages.warning(request, " · ".join(str(w)[:160] for w in api_warnings[:2]))

    products = [_map_product(p) for p in raw_products if isinstance(p, dict)]
    # Faol mahsulotlarni birinchi ko'rsatish
    products.sort(key=lambda p: (not p.is_active, p.name.lower()))
    products_by_id = {str(p.id): p for p in products}

    brand_choices = sorted({p.brand for p in products if p.brand})
    category_choices = sorted({p.category for p in products if p.category})

    day_sales_raw = [s for s in day_sales_raw if isinstance(s, dict)]
    day_sales_raw.sort(
        key=lambda s: _parse_dt(s.get("completed_at") or s.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Detail — faqat kerakli bo'limlarda
    need_detail_ids = []
    if need_sale_details:
        need_detail_ids = [str(s.get("id")) for s in day_sales_raw if s.get("id")]
    details = (
        _fetch_sale_details(token, server, need_detail_ids, limit=40)
        if need_detail_ids
        else {}
    )
    cashier = _cashier_name(request)

    day_sales_list = []
    day_sales_payload = []
    day_gross = Decimal("0")
    day_cost_total = Decimal("0")
    day_pay_totals = defaultdict(Decimal)
    for s in day_sales_raw:
        sid = str(s.get("id"))
        detail = details.get(sid) or s
        cost = (
            _estimate_sale_cost(detail, products_by_id)
            if detail.get("items")
            else Decimal("0")
        )
        total = _dec(detail.get("total") or s.get("total"))
        profit = total - cost
        method = detail.get("payment_type") or s.get("payment_type") or "cash"
        dt = _parse_dt(
            detail.get("completed_at") or detail.get("created_at") or s.get("created_at")
        )
        row = SimpleNamespace(
            id=sid,
            created_at=dt or timezone.now(),
            customer_name=detail.get("customer_name") or s.get("customer_name") or "",
            total_amount=total,
            total_cost=cost,
            profit=profit,
            payment_method=method,
        )
        day_sales_list.append(row)
        day_gross += total
        day_cost_total += cost
        day_pay_totals[_payment_label(method)] += total
        day_sales_payload.append(
            _serialize_sale_payload(
                detail if detail.get("items") is not None else {**s, "items": []},
                cashier,
                products_by_id,
            )
        )

    day_profit_total = day_gross - day_cost_total

    if section == "sales" and request.GET.get("export") == "excel":
        return _export_daily_sales_excel(day_sales_payload, sale_date, cashier)

    # Top mahsulotlar — backend stats; bosh bo'lsa oxirgi cheklar
    product_qty = defaultdict(Decimal)
    product_rev = defaultdict(Decimal)
    item_rows = []
    top_payload = {"items": []}
    if need_top_stats:
        try:
            top_payload = tezpos_api.get_top_products(token, server, days=30, limit=100)
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            top_payload = {"items": []}

    for row in top_payload.get("items") or []:
        pid = str(row.get("product_id") or "")
        qty = _dec(row.get("quantity"))
        p = products_by_id.get(pid)
        if not pid or not p:
            continue
        product_qty[pid] = qty
        product_rev[pid] = qty * p.selling_price
        item_rows.append(
            {
                "product_id": pid,
                "quantity": qty,
                "unit_price": p.selling_price,
                "day": today,
                "name": p.name,
            }
        )

    if need_top_stats and not product_rev:
        recent_ids = []
        for s in raw_sales:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            sid = str(s["id"])
            if sid not in details:
                recent_ids.append(sid)
            if len(recent_ids) >= 35:
                break
        if recent_ids:
            details.update(_fetch_sale_details(token, server, recent_ids, limit=35))
        for detail in details.values():
            d = _sale_day(detail)
            for item in detail.get("items") or []:
                pid = str(item.get("product_id") or "")
                qty = _dec(item.get("quantity"))
                unit_price = _dec(item.get("unit_price"))
                if pid:
                    product_qty[pid] += qty
                    product_rev[pid] += qty * unit_price
                item_rows.append(
                    {
                        "product_id": pid,
                        "quantity": qty,
                        "unit_price": unit_price,
                        "day": d,
                        "name": item.get("product_name") or "",
                    }
                )

    sales_stats = _build_charts_from_sales(
        [s for s in raw_sales if isinstance(s, dict)], today
    )
    chart_labels = sales_stats["d7"]["labels"]
    chart_totals = sales_stats["d7"]["totals"]
    chart_counts = sales_stats["d7"]["counts"]
    d_pack, w_pack, m_pack = sales_stats["d7"], sales_stats["m3"], sales_stats["m6"]

    gross = sum((_dec(s.get("total")) for s in raw_sales if isinstance(s, dict)), Decimal("0"))

    # Bugungi ko'rsatkichlar
    if sale_date == today:
        today_count = len(day_sales_list)
        today_gross = day_gross
        today_profit = day_profit_total
        cost = day_cost_total
        profit = day_profit_total
    else:
        today_sales = [s for s in raw_sales if isinstance(s, dict) and _sale_day(s) == today]
        today_count = len(today_sales)
        today_gross = sum((_dec(s.get("total")) for s in today_sales), Decimal("0"))
        today_profit = today_gross
        cost = day_cost_total
        profit = day_profit_total if day_sales_list else Decimal("0")

    margin = (profit / day_gross * 100) if day_gross and profit else Decimal("0")
    if not day_sales_list:
        # Davr bo'yicha tannarx yo'q — sof foydani 0 qoldiramiz
        if not profit:
            cost = Decimal("0")
            profit = Decimal("0")
            margin = Decimal("0")

    share_items = []
    for pid, rev in sorted(product_rev.items(), key=lambda x: x[1], reverse=True)[:200]:
        p = products_by_id.get(pid)
        if p:
            share_items.append(
                {
                    "name": p.name,
                    "image": p.display_image,
                    "stock": float(p.stock_qty),
                    "wholesale": float(p.wholesale_price or p.cost_price),
                    "selling": float(p.selling_price),
                    "revenue": float(rev),
                }
            )
    if not share_items:
        # Mahsulotlar bo'yicha ombor qiymati (sotuv bo'lmasa ham to'ldirish)
        for p in sorted(products, key=lambda x: x.selling_price * max(x.stock_qty, 0), reverse=True)[:50]:
            share_items.append(
                {
                    "name": p.name,
                    "image": p.display_image,
                    "stock": float(p.stock_qty),
                    "wholesale": float(p.wholesale_price or p.cost_price),
                    "selling": float(p.selling_price),
                    "revenue": float(p.selling_price * max(p.stock_qty, Decimal("0"))),
                }
            )
    if not share_items:
        share_items = [
            {
                "name": "Hozircha ma'lumot yo'q",
                "image": "",
                "stock": 0,
                "wholesale": 0,
                "selling": 0,
                "revenue": 1,
            }
        ]

    top_products = []
    for pid, rev in sorted(product_rev.items(), key=lambda x: x[1], reverse=True)[:100]:
        p = products_by_id.get(pid)
        if not p:
            continue
        top_products.append(
            {
                "name": p.name,
                "image": p.display_image,
                "qty": float(product_qty[pid]),
                "revenue": float(rev),
                "stock": float(p.stock_qty),
                "wholesale": float(p.wholesale_price or p.cost_price),
                "selling": float(p.selling_price),
            }
        )

    products_payload = [
        {
            "id": p.id,
            "name": p.name,
            "barcode": p.barcode or "",
            "barcodes": p.barcode_list,
            "unit": p.unit or "dona",
            "category": p.category or "",
            "brand": p.brand or "",
            "selling_price": float(p.selling_price),
            "wholesale_price": float(p.wholesale_price or 0),
            "cost_price": float(p.cost_price or 0),
            "stock_qty": float(p.stock_qty or 0),
            "min_stock": float(p.min_stock or 0),
            "is_favorite": bool(p.is_favorite),
            "image": p.display_image,
            "image_url": p.image_url or "",
            "images": [
                {"id": img.id, "url": img.image.url, "is_primary": img.is_primary}
                for img in (p.images.all() if hasattr(p.images, "all") else [])
            ],
        }
        for p in products
    ]

    cust_agg = defaultdict(lambda: {"orders": 0, "total": Decimal("0")})
    for s in raw_sales:
        if not isinstance(s, dict):
            continue
        name = (s.get("customer_name") or "").strip()
        if not name:
            continue
        cust_agg[name]["orders"] += 1
        cust_agg[name]["total"] += _dec(s.get("total"))
    customer_rows = [
        {"customer_name": n, "orders": v["orders"], "total": float(v["total"])}
        for n, v in sorted(cust_agg.items(), key=lambda x: x[1]["total"], reverse=True)[:100]
    ]

    abc_rows, abc_matrix, abc_total = _build_abc_xyz(products, item_rows, today)
    near_min = _build_near_min_stock(products)
    signals = near_min
    low_stock = near_min[:8]

    section = section_force or request.GET.get("section", "overview")
    allowed = {
        "overview",
        "sales",
        "inventory",
        "products",
        "reports",
        "abc",
        "signals",
        "labels",
        "tops",
    }
    if section not in allowed:
        section = "overview"

    return render(
        request,
        "accounts/cabinet.html",
        {
            "tenant": tenant,
            "form": form,
            "products": products,
            "products_json": json.dumps(products_payload, ensure_ascii=False),
            "brand_choices": brand_choices,
            "category_choices": category_choices,
            "all_products_count": len(products),
            "form_errors": form.errors if request.method == "POST" else None,
            "section": section,
            "total_sales": len(raw_sales),
            "gross": gross,
            "cost": cost,
            "profit": profit,
            "margin": margin,
            "today_count": today_count,
            "today_gross": today_gross,
            "today_profit": today_profit,
            "recent_sales": day_sales_list,
            "sale_date": sale_date,
            "sale_date_iso": sale_date.isoformat(),
            "day_sales_count": len(day_sales_list),
            "day_gross": day_gross,
            "day_cost_total": day_cost_total,
            "day_profit_total": day_profit_total,
            "day_pay_totals": dict(day_pay_totals),
            "day_sales_json": json.dumps(day_sales_payload, ensure_ascii=False),
            "cashier_name": cashier,
            "low_stock": low_stock,
            "signals": signals,
            "active_signals_count": len(signals),
            "near_min_json": json.dumps(signals, ensure_ascii=False),
            "abc_rows": abc_rows,
            "abc_matrix": abc_matrix,
            "abc_total": abc_total,
            "top_products_json": json.dumps(top_products, ensure_ascii=False),
            "top_customers_json": json.dumps(customer_rows, ensure_ascii=False),
            "chart_labels_json": json.dumps(chart_labels, ensure_ascii=False),
            "chart_totals_json": json.dumps(chart_totals),
            "chart_counts_json": json.dumps(chart_counts),
            "sales_stats_json": json.dumps(sales_stats, ensure_ascii=False),
            "share_items_json": json.dumps(share_items, ensure_ascii=False),
            "report_charts_json": json.dumps(
                {
                    "daily": {
                        "labels": d_pack["labels"],
                        "totals": d_pack["totals"],
                        "counts": d_pack["counts"],
                    },
                    "weekly": {
                        "labels": w_pack["labels"],
                        "totals": w_pack["totals"],
                        "counts": w_pack["counts"],
                    },
                    "monthly": {
                        "labels": m_pack["labels"],
                        "totals": m_pack["totals"],
                        "counts": m_pack["counts"],
                    },
                },
                ensure_ascii=False,
            ),
        },
    )
