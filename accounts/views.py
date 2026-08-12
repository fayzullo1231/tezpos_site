"""Shaxsiy kabinet — TezPOS backend ma'lumotlari."""
from __future__ import annotations

from io import BytesIO
import csv
import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean, pstdev
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

import os
import re

from . import tezpos_api
from .auth_views import (
    SESSION_DISPLAY,
    SESSION_SERVER,
    SESSION_TOKEN,
    clear_tezpos_session,
    session_has_tezpos,
)
from .models import DesktopInstaller, TenantProfile


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


@require_GET
def download_installer(request):
    """Faol .exe ni yuklab beradi (Install tugmasi)."""
    installer = DesktopInstaller.get_active()
    if not installer or not installer.file:
        raise Http404("Installer hali yuklanmagan.")
    try:
        fh = installer.file.open("rb")
    except Exception as exc:
        raise Http404("Fayl topilmadi.") from exc

    filename = os.path.basename(installer.file.name) or "TezPOS-Setup.exe"
    if not filename.lower().endswith(".exe"):
        filename = f"{filename}.exe"
    response = FileResponse(fh, as_attachment=True, filename=filename)
    response["Content-Type"] = "application/octet-stream"
    return response


def _dec(value, default="0") -> Decimal:
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value).replace(",", ".").replace(" ", "").replace("\u00a0", ""))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


# TezPOS javoblarini qisqa muddat xotirada saqlash (SSR/AJAX tezligi)
_TEZPOS_MEMO: dict[str, tuple[float, object]] = {}


def _memo_get(key: str, ttl: float, loader):
    now = time.time()
    hit = _TEZPOS_MEMO.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = loader()
    _TEZPOS_MEMO[key] = (now, val)
    # Juda katta bo‘lib ketmasin
    if len(_TEZPOS_MEMO) > 96:
        oldest = sorted(_TEZPOS_MEMO.items(), key=lambda x: x[1][0])[:24]
        for k, _ in oldest:
            _TEZPOS_MEMO.pop(k, None)
    return val


def _products_payload_list(products: list) -> list[dict]:
    out = []
    for p in products:
        images = []
        try:
            if hasattr(p, "images") and hasattr(p.images, "all"):
                images = [
                    {"id": img.id, "url": img.image.url, "is_primary": img.is_primary}
                    for img in p.images.all()
                ]
        except Exception:
            images = []
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "barcode": p.barcode or "",
                "barcodes": getattr(p, "barcode_list", []) or [],
                "unit": p.unit or "dona",
                "category": p.category or "",
                "brand": p.brand or "",
                "selling_price": float(p.selling_price),
                "wholesale_price": float(p.wholesale_price or 0),
                "cost_price": float(p.cost_price or 0),
                "list_prices": {
                    str(k): float(v) for k, v in (p.list_prices or {}).items()
                },
                "stock_qty": float(p.stock_qty or 0),
                "min_stock": float(p.min_stock or 0),
                "is_favorite": bool(p.is_favorite),
                "image": p.display_image,
                "image_url": getattr(p, "image_url", "") or "",
                "images": images,
            }
        )
    return out


@login_required
@require_GET
def cabinet_catalog(request):
    """Mahsulotlar + narxlar ro‘yxati (AJAX) — SSR o‘rniga, kesh bilan."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_p = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|products|4",
                    600.0,
                    lambda: tezpos_api.get_products(token, server, max_pages=4),
                )
            )
            fut_pl = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|price_lists",
                    600.0,
                    lambda: tezpos_api.get_price_lists(token, server) or [],
                )
            )
            raw_products = fut_p.result() or []
            raw_pl = fut_pl.result() or []
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    products = [_map_product(p) for p in raw_products if isinstance(p, dict)]
    products.sort(key=lambda p: (not p.is_active, p.name.lower()))
    price_lists = [
        pl for pl in raw_pl if isinstance(pl, dict) and pl.get("is_active", True)
    ]
    near_min = _build_near_min_stock(products)
    return JsonResponse(
        {
            "ok": True,
            "products": _products_payload_list(products),
            "priceLists": [
                {
                    "id": str(pl.get("id") or ""),
                    "name": (pl.get("name") or "").strip() or "Narxlar",
                    "is_selling": bool(pl.get("is_selling")),
                }
                for pl in price_lists
                if str(pl.get("id") or "")
            ],
            "nearMin": near_min,
            "cached": True,
        }
    )


@login_required
@require_GET
def cabinet_warm(request):
    """API memo ni fonida isitish — navigatsiya kutmasin."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    today = timezone.localdate().isoformat()

    def _warm() -> None:
        try:
            _memo_get(
                f"{memo_prefix}|products|4",
                600.0,
                lambda: tezpos_api.get_products(token, server, max_pages=4),
            )
        except Exception:
            pass
        try:
            _memo_get(
                f"{memo_prefix}|price_lists",
                600.0,
                lambda: tezpos_api.get_price_lists(token, server) or [],
            )
        except Exception:
            pass
        try:
            _memo_get(
                f"{memo_prefix}|day|{today}",
                120.0,
                lambda: tezpos_api.get_sales_for_day(token, server, today),
            )
        except Exception:
            pass
        try:
            _memo_get(
                f"{memo_prefix}|sales|{today}|{today}|1",
                90.0,
                lambda: tezpos_api.get_sales(
                    token,
                    server,
                    date_from=today,
                    date_to=today,
                    timeout=10,
                    max_pages=1,
                ),
            )
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True, name="tezpos-warm").start()
    return JsonResponse({"ok": True, "warming": True})


@login_required
@require_GET
def cabinet_day_sales(request):
    """Kunlik sotuvlar ro‘yxati (AJAX) — sahifa darhol ochilsin."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    sale_date = _parse_sale_date(request.GET.get("sale_date"))
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    try:
        day_sales_raw = _memo_get(
            f"{memo_prefix}|day|{sale_date.isoformat()}",
            90.0,
            lambda: tezpos_api.get_sales_for_day(token, server, sale_date.isoformat()),
        )
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    day_sales_raw = [s for s in (day_sales_raw or []) if isinstance(s, dict)]
    day_sales_raw.sort(
        key=lambda s: _parse_dt(s.get("completed_at") or s.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    margin_ratio = Decimal("0.25")
    payload = []
    day_gross = Decimal("0")
    for s in day_sales_raw:
        total = _dec(s.get("total"))
        day_gross += total
        profit = total * margin_ratio
        dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
        method = s.get("payment_type") or s.get("payment_method") or "cash"
        payload.append(
            {
                "id": str(s.get("id") or ""),
                "time": timezone.localtime(dt).strftime("%H:%M") if dt else "",
                "customer": s.get("customer_name") or "",
                "total": float(total),
                "cost": float(total - profit),
                "profit": float(profit),
                "payment": method,
                "payment_label": PAYMENT_LABELS.get(
                    str(method).strip().lower(), str(method or "—")
                ),
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "sale_date": sale_date.isoformat(),
            "count": len(payload),
            "gross": float(day_gross),
            "profit": float(day_gross * margin_ratio),
            "sales": payload,
        }
    )


def _collect_shifts_payload(token: str, server: str, *, days: int = 21) -> tuple[list[dict], str]:
    """Smenalar ro‘yxati (API yoki sotuvlardan)."""
    today = timezone.localdate()
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    date_from = (today - timedelta(days=days)).isoformat()
    date_to = today.isoformat()

    raw_shifts_api: list = []
    try:
        raw_shifts_api = _memo_get(
            f"{memo_prefix}|shifts|{date_from}|{date_to}",
            90.0,
            lambda: tezpos_api.get_shifts(
                token,
                server,
                date_from=date_from,
                date_to=date_to,
                timeout=10,
                max_pages=2,
            )
            or [],
        )
    except (tezpos_api.TezPosApiError, TimeoutError, OSError):
        raw_shifts_api = []

    margin_ratio = Decimal("0.25")
    try:
        products_raw = _memo_get(
            f"{memo_prefix}|products|2",
            600.0,
            lambda: tezpos_api.get_products(token, server, max_pages=2) or [],
        )
        products = [_map_product(p) for p in (products_raw or []) if isinstance(p, dict)]
        if products:
            margin_ratio = _catalog_margin_ratio(products) or Decimal("0.25")
    except Exception:
        pass

    shifts_payload: list[dict] = []
    shifts_source = "none"
    sales_for_shifts: list = []

    if raw_shifts_api:
        shifts_source = "api"
        try:
            sales_for_shifts = _memo_get(
                f"{memo_prefix}|sales|{date_from}|{date_to}|2",
                60.0,
                lambda: tezpos_api.get_sales(
                    token,
                    server,
                    date_from=date_from,
                    date_to=date_to,
                    timeout=12,
                    max_pages=2,
                )
                or [],
            )
        except Exception:
            sales_for_shifts = []
        sales_for_shifts = [s for s in sales_for_shifts if isinstance(s, dict)]
        for raw in raw_shifts_api:
            if not isinstance(raw, dict):
                continue
            sh = _normalize_api_shift(raw, today)
            sh = _enrich_shift_with_sales(
                sh, sales_for_shifts, margin_ratio=margin_ratio
            )
            sh.pop("raw", None)
            shifts_payload.append(sh)

    if not shifts_payload:
        shifts_source = "sales"
        if not sales_for_shifts:
            try:
                sales_for_shifts = _memo_get(
                    f"{memo_prefix}|sales|{date_from}|{date_to}|2",
                    60.0,
                    lambda: tezpos_api.get_sales(
                        token,
                        server,
                        date_from=date_from,
                        date_to=date_to,
                        timeout=12,
                        max_pages=2,
                    )
                    or [],
                )
            except Exception:
                sales_for_shifts = []
        shifts_payload = _build_shifts_from_sales(
            [s for s in sales_for_shifts if isinstance(s, dict)],
            today=today,
            margin_ratio=margin_ratio,
        )

    shifts_payload.sort(key=lambda s: s.get("opened_at") or "", reverse=True)
    return shifts_payload[:60], shifts_source


@login_required
@require_GET
def cabinet_shifts(request):
    """Smenalar ro‘yxati (AJAX) — sahifa darhol ochilsin."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    try:
        shifts, source = _collect_shifts_payload(token, server, days=21)
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    return JsonResponse(
        {
            "ok": True,
            "shifts": shifts,
            "source": source,
            "count": len(shifts),
        }
    )


@login_required
@require_GET
def cabinet_reports(request):
    """Hisobotlar grafigi (AJAX) — sahifa kutmasin."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    today = timezone.localdate()
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    start = today - timedelta(days=29)
    try:
        sales = _memo_get(
            f"{memo_prefix}|sales|{start.isoformat()}|{today.isoformat()}|2",
            90.0,
            lambda: tezpos_api.get_sales(
                token,
                server,
                date_from=start.isoformat(),
                date_to=today.isoformat(),
                timeout=14,
                max_pages=2,
            )
            or [],
        )
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    sales = [s for s in (sales or []) if isinstance(s, dict)]
    charts = _build_charts_from_sales(sales, today)
    d_pack = charts.get("d7") or {"labels": [], "totals": [], "counts": []}
    # Hisobot UI: daily = soatlik bugun, weekly = 7 kun, monthly = 6 oy
    daily = _chart_pack_for_dates(
        [s for s in sales if _sale_day(s) == today], today, today
    )
    weekly = _chart_pack_for_dates(sales, today - timedelta(days=6), today)
    monthly = charts.get("m6") or charts.get("m3") or d_pack

    margin_ratio = Decimal("0.25")
    try:
        products_raw = _memo_get(
            f"{memo_prefix}|products|2",
            600.0,
            lambda: tezpos_api.get_products(token, server, max_pages=2) or [],
        )
        products = [_map_product(p) for p in (products_raw or []) if isinstance(p, dict)]
        if products:
            margin_ratio = _catalog_margin_ratio(products) or Decimal("0.25")
    except Exception:
        pass

    gross = float(sum((_dec(s.get("total")) for s in sales), Decimal("0")))
    checks = len(sales)
    profit = gross * float(margin_ratio)
    today_sales = [s for s in sales if _sale_day(s) == today]
    today_gross = float(sum((_dec(s.get("total")) for s in today_sales), Decimal("0")))
    today_profit = today_gross * float(margin_ratio)

    return JsonResponse(
        {
            "ok": True,
            "reports": {
                "daily": daily,
                "weekly": weekly,
                "monthly": {
                    "labels": monthly.get("labels") or [],
                    "totals": monthly.get("totals") or [],
                    "counts": monthly.get("counts") or [],
                },
            },
            "summary": {
                "checks": checks,
                "gross": gross,
                "cost": gross - profit,
                "profit": profit,
                "margin": (profit / gross * 100.0) if gross else 0.0,
                "today_profit": today_profit,
                "today_count": len(today_sales),
                "today_gross": today_gross,
            },
        }
    )


@login_required
@require_GET
def cabinet_abc(request):
    """ABC-XYZ (AJAX)."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    today = timezone.localdate()
    memo_prefix = f"{server}|{(token or '')[-12:]}"
    start = today - timedelta(days=14)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_p = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|products|4",
                    600.0,
                    lambda: tezpos_api.get_products(token, server, max_pages=4) or [],
                )
            )
            fut_s = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|sales|{start.isoformat()}|{today.isoformat()}|2",
                    90.0,
                    lambda: tezpos_api.get_sales(
                        token,
                        server,
                        date_from=start.isoformat(),
                        date_to=today.isoformat(),
                        timeout=14,
                        max_pages=2,
                    )
                    or [],
                )
            )
            products_raw = fut_p.result() or []
            sales = fut_s.result() or []
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    sales = [s for s in sales if isinstance(s, dict)]
    item_rows = []
    sale_ids = [str(s.get("id")) for s in sales if s.get("id")][:24]
    details = _fetch_sale_details(
        token,
        server,
        sale_ids,
        limit=24,
        per_sale_timeout=3.0,
        overall_timeout=8.0,
    )
    for detail in details.values():
        for item in detail.get("items") or []:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("product_id") or item.get("product") or "")
            if not pid or pid.startswith("{"):
                name = (item.get("product_name") or item.get("name") or "").strip()
                if not name:
                    continue
                pid = f"name:{name.casefold()}"
            qty = _dec(item.get("quantity"))
            unit_price = _dec(item.get("unit_price"))
            item_rows.append(
                {
                    "product_id": pid,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "day": _sale_day(detail) or today,
                }
            )
    abc_rows, abc_matrix, abc_total = _build_abc_xyz(products, item_rows, today)
    rows_out = []
    for row in abc_rows[:200]:
        p = row.get("product")
        rows_out.append(
            {
                "name": getattr(p, "name", None) or row.get("name") or "—",
                "abc": row.get("abc"),
                "xyz": row.get("xyz"),
                "group": row.get("group"),
                "revenue": float(row.get("revenue") or 0),
                "share": float(row.get("share") or 0),
                "stock": float(getattr(p, "stock_qty", 0) or 0),
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "rows": rows_out,
            "matrix": abc_matrix,
            "total": float(abc_total or 0),
        }
    )


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

    list_prices_raw = raw.get("list_prices") or {}
    list_prices: dict[str, Decimal] = {}
    if isinstance(list_prices_raw, dict):
        for k, v in list_prices_raw.items():
            list_prices[str(k)] = _dec(v)
    wholesale = Decimal("0")
    if list_prices:
        try:
            wholesale = min(list_prices.values())
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
        list_prices=list_prices,
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


SELLING_LIST_ID = "__selling__"
OTHER_LIST_ID = "__other__"


def _range_window(key: str, today: date) -> tuple[date, date]:
    if key == "d1":
        return today, today
    if key == "d7":
        return today - timedelta(days=6), today
    if key == "d15":
        return today - timedelta(days=14), today
    if key == "d30":
        return today - timedelta(days=29), today
    if key == "m1":
        return today.replace(day=1), today
    if key == "m3":
        return today - timedelta(weeks=12), today
    if key in ("m6", "y1"):
        months = 6 if key == "m6" else 12
        y, m = today.year, today.month
        m -= months - 1
        while m <= 0:
            m += 12
            y -= 1
        return date(y, m, 1), today
    return today - timedelta(days=6), today


def _attach_range_summaries(
    sales_stats: dict, sales: list[dict], today: date, margin_ratio: Decimal
) -> dict:
    ratio = float(margin_ratio or 0)
    sale_dates = [
        d
        for s in sales
        if isinstance(s, dict)
        for d in [_sale_day(s)]
        if d
    ]
    earliest = min(sale_dates) if sale_dates else None
    for key, pack in sales_stats.items():
        start, end = _range_window(key, today)
        checks = 0
        gross = 0.0
        for s in sales:
            if not isinstance(s, dict):
                continue
            d = _sale_day(s)
            if not d or d < start or d > end:
                continue
            checks += 1
            gross += float(_dec(s.get("total")))
        profit = gross * ratio
        pack["summary"] = {
            "checks": checks,
            "gross": gross,
            "profit": profit,
            "margin": (profit / gross * 100.0) if gross > 0 else 0.0,
        }
        # Yuklangan sotuvlar oynasi to'liq qoplamasa — AJAX kerak
        pack["partial"] = bool(earliest is None or earliest > start)
    return sales_stats


def _chart_pack_from_sales(sales: list[dict], today: date, range_key: str) -> dict:
    """AJAX uchun bitta davr grafigi."""
    stats = _build_charts_from_sales(sales, today)
    pack = stats.get(range_key) or {"labels": [], "totals": [], "counts": []}
    return {
        "labels": pack.get("labels") or [],
        "totals": pack.get("totals") or [],
        "counts": pack.get("counts") or [],
    }


def _parse_iso_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _chart_pack_for_dates(sales: list[dict], start: date, end: date) -> dict:
    """Tanlangan kun yoki oralik uchun grafik (soatlik / kunlik / haftalik / oylik)."""
    if end < start:
        start, end = end, start
    days = (end - start).days + 1
    sales = [s for s in sales if isinstance(s, dict)]

    if days <= 1:
        totals_map: dict[int, float] = defaultdict(float)
        counts_map: dict[int, int] = defaultdict(int)
        for s in sales:
            if _sale_day(s) != start:
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

    if days <= 62:
        totals_map_d: dict[date, float] = defaultdict(float)
        counts_map_d: dict[date, int] = defaultdict(int)
        for s in sales:
            d = _sale_day(s)
            if not d or d < start or d > end:
                continue
            totals_map_d[d] += float(_dec(s.get("total")))
            counts_map_d[d] += 1
        labels, totals, counts = [], [], []
        for i in range(days):
            day = start + timedelta(days=i)
            labels.append(day.strftime("%d.%m"))
            totals.append(totals_map_d.get(day, 0.0))
            counts.append(counts_map_d.get(day, 0))
        return {"labels": labels, "totals": totals, "counts": counts}

    if days <= 180:
        totals_map_w: dict[date, float] = defaultdict(float)
        counts_map_w: dict[date, int] = defaultdict(int)
        for s in sales:
            d = _sale_day(s)
            if not d or d < start or d > end:
                continue
            week_start = d - timedelta(days=d.weekday())
            totals_map_w[week_start] += float(_dec(s.get("total")))
            counts_map_w[week_start] += 1
        labels, totals, counts = [], [], []
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            labels.append(cursor.strftime("%d.%m"))
            totals.append(totals_map_w.get(cursor, 0.0))
            counts.append(counts_map_w.get(cursor, 0))
            cursor += timedelta(weeks=1)
        return {"labels": labels, "totals": totals, "counts": counts}

    totals_map_m: dict[date, float] = defaultdict(float)
    counts_map_m: dict[date, int] = defaultdict(int)
    for s in sales:
        d = _sale_day(s)
        if not d or d < start or d > end:
            continue
        key = date(d.year, d.month, 1)
        totals_map_m[key] += float(_dec(s.get("total")))
        counts_map_m[key] += 1
    labels, totals, counts = [], [], []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        key = date(y, m, 1)
        labels.append(key.strftime("%m.%Y"))
        totals.append(totals_map_m.get(key, 0.0))
        counts.append(counts_map_m.get(key, 0))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return {"labels": labels, "totals": totals, "counts": counts}


def _products_by_name(products: list) -> dict[str, SimpleNamespace]:
    out: dict[str, SimpleNamespace] = {}
    for p in products:
        name = (getattr(p, "name", "") or "").strip()
        if not name:
            continue
        if name not in out:
            out[name] = p
        key = " ".join(name.casefold().split())
        if key and key not in out:
            out[key] = p
    return out


def _find_product(
    products_by_id: dict[str, SimpleNamespace],
    products_by_name: dict[str, SimpleNamespace] | None,
    *,
    product_id: str = "",
    product_name: str = "",
) -> SimpleNamespace | None:
    if product_id and str(product_id) in products_by_id:
        return products_by_id[str(product_id)]
    by_name = products_by_name or {}
    name = (product_name or "").strip()
    if not name:
        return None
    if name in by_name:
        return by_name[name]
    key = " ".join(name.casefold().split())
    if key in by_name:
        return by_name[key]
    # Qisman moslik (chekdagi nom biroz farq qilsa)
    for k, p in by_name.items():
        if not isinstance(k, str):
            continue
        kk = " ".join(k.casefold().split())
        if kk and (kk in key or key in kk) and abs(len(kk) - len(key)) <= 8:
            return p
    return None


def _price_within(unit: float, catalog: float, ratio: float = 0.01, floor: float = 1.0) -> bool:
    """Birlik narxi katalog narxiga mos keladimi."""
    if catalog <= 0:
        return False
    return abs(unit - catalog) <= max(floor, abs(catalog) * ratio)


def _is_api_selling_list(pl: dict) -> bool:
    name = (pl.get("name") or "").strip().casefold()
    return bool(
        pl.get("is_selling")
        or name in {"sotuv", "sotish", "sotuv narxi", "retail", "selling"}
    )


def _match_price_list_id(
    unit_price: Decimal,
    product: SimpleNamespace | None,
    price_lists: list[dict],
) -> str:
    """
    Birlik narxini Sotuv yoki Optom (narxlar ro'yxati) ga biriktiradi.
    Mahsulot topilsa — eng yaqin katalog narxi (Boshqa bo'lmaydi).
    Topilmasa — Sotuv (oddiy chakana savdo).
    """
    if not product:
        return SELLING_LIST_ID

    up = float(unit_price)
    if up <= 0:
        return SELLING_LIST_ID

    selling = float(getattr(product, "selling_price", 0) or 0)
    list_prices = getattr(product, "list_prices", None) or {}
    candidates: list[tuple[str, float]] = []
    if selling > 0:
        candidates.append((SELLING_LIST_ID, selling))

    for pl in price_lists:
        lid = str(pl.get("id") or "")
        if not lid or lid not in list_prices:
            continue
        if _is_api_selling_list(pl):
            continue
        lp = float(list_prices[lid] or 0)
        if lp > 0:
            candidates.append((lid, lp))

    if not candidates:
        return SELLING_LIST_ID

    # Eng yaqin narx
    best_id, best_price = min(candidates, key=lambda x: abs(up - x[1]))
    best_dist = abs(up - best_price)

    # Agar sotuv ham deyarli yaqin bo'lsa — Sotuv ustun (bir xil masofa)
    if selling > 0:
        sell_dist = abs(up - selling)
        if sell_dist <= best_dist + 0.5:
            # Qattiq moslik: sotuv ±1.5% ichida
            if _price_within(up, selling, ratio=0.015, floor=1.0):
                return SELLING_LIST_ID
            # Optom aniqroq (kamida 1.5x yaqinroq) bo'lmasa — Sotuv
            if best_id != SELLING_LIST_ID and best_dist * 1.5 < sell_dist:
                return best_id
            return SELLING_LIST_ID

    return best_id


def _aggregate_price_list_stats(
    sale_details: list[dict],
    products_by_id: dict[str, SimpleNamespace],
    products_by_name: dict[str, SimpleNamespace],
    price_lists: list[dict],
) -> list[dict]:
    """Har bir narxlar ro'yxati + sotuv narxi bo'yicha qty/chek/foyda/marja."""
    buckets: dict[str, dict] = {
        SELLING_LIST_ID: {
            "id": SELLING_LIST_ID,
            "name": "Sotuv",
            "qty": 0.0,
            "checks": set(),
            "revenue": 0.0,
            "cost": 0.0,
        },
    }
    selling_list_ids = {
        str(pl.get("id")) for pl in price_lists if pl.get("id") and _is_api_selling_list(pl)
    }
    for pl in price_lists:
        lid = str(pl.get("id") or "")
        if not lid or lid in selling_list_ids or _is_api_selling_list(pl):
            continue
        buckets[lid] = {
            "id": lid,
            "name": (pl.get("name") or "Ro‘yxat").strip() or "Ro‘yxat",
            "qty": 0.0,
            "checks": set(),
            "revenue": 0.0,
            "cost": 0.0,
        }

    for detail in sale_details:
        if not isinstance(detail, dict):
            continue
        sid = str(detail.get("id") or "")
        for item in detail.get("items") or []:
            qty_dec = _dec(item.get("quantity"))
            qty = float(qty_dec)
            unit_price = _dec(item.get("unit_price"))
            line_total_dec = _dec(item.get("total"), str(qty_dec * unit_price))
            line_total = float(line_total_dec)
            if unit_price <= 0 and qty_dec > 0 and line_total_dec > 0:
                unit_price = (line_total_dec / qty_dec).quantize(Decimal("0.01"))
            pid = str(item.get("product_id") or item.get("product") or "")
            if pid.startswith("{") or pid == "None":
                pid = ""
            name = (item.get("product_name") or item.get("name") or "").strip()
            p = _find_product(
                products_by_id,
                products_by_name,
                product_id=pid,
                product_name=name,
            )
            raw_pl = (
                item.get("price_list_id")
                or item.get("price_list")
                or item.get("list_id")
            )
            if isinstance(raw_pl, dict):
                raw_pl = raw_pl.get("id")
            list_id = str(raw_pl).strip() if raw_pl not in (None, "") else ""
            if list_id in ("selling", "retail", SELLING_LIST_ID) or list_id in selling_list_ids:
                list_id = SELLING_LIST_ID
            elif list_id and list_id in buckets:
                pass
            else:
                list_id = _match_price_list_id(unit_price, p, price_lists)
            if list_id not in buckets:
                list_id = SELLING_LIST_ID
            unit_cost = _dec(
                item.get("unit_cost")
                or item.get("cost_price")
                or item.get("purchase_price")
                or item.get("buy_price")
            )
            if unit_cost <= 0 and p:
                unit_cost = p.cost_price or Decimal("0")
            bucket = buckets[list_id]
            bucket["qty"] += qty
            bucket["revenue"] += line_total
            bucket["cost"] += float(unit_cost) * qty
            if sid:
                bucket["checks"].add(sid)

    out = []
    order = [SELLING_LIST_ID] + [
        str(pl.get("id"))
        for pl in price_lists
        if pl.get("id") and str(pl.get("id")) in buckets
    ]
    seen = set()
    total_rev = 0.0
    total_profit = 0.0
    total_cost = 0.0
    total_qty = 0.0
    all_checks: set = set()
    for lid in order:
        if lid in seen or lid not in buckets:
            continue
        seen.add(lid)
        b = buckets[lid]
        rev = float(b["revenue"])
        if rev <= 0:
            continue
        cost = float(b["cost"])
        profit = rev - cost
        total_rev += rev
        total_profit += profit
        total_cost += cost
        total_qty += float(b["qty"])
        all_checks |= b["checks"]
        markup = (profit / cost * 100.0) if cost > 0 else 0.0
        margin = (profit / rev * 100.0) if rev > 0 else 0.0
        out.append(
            {
                "id": b["id"],
                "name": b["name"],
                "qty": round(float(b["qty"]), 3),
                "checks": len(b["checks"]),
                "revenue": rev,
                "cost": cost,
                "profit": profit,
                "margin": margin,
                "markup": markup,
                "share": 0.0,
                "is_total": False,
            }
        )
    if total_rev > 0:
        for row in out:
            row["share"] = row["revenue"] / total_rev * 100.0
    if out:
        out.append(
            {
                "id": "__all__",
                "name": "Jami",
                "qty": round(total_qty, 3),
                "checks": len(all_checks),
                "revenue": total_rev,
                "cost": total_cost,
                "profit": total_profit,
                "margin": (total_profit / total_rev * 100.0) if total_rev > 0 else 0.0,
                "markup": (total_profit / total_cost * 100.0) if total_cost > 0 else 0.0,
                "share": 100.0,
                "is_total": True,
            }
        )
    return out


def _scale_price_list_stats(
    lists: list[dict],
    target_gross: float,
    target_checks: int,
) -> list[dict]:
    """
    Namunadagi Sotuv/Optom ulushini to'liq davr tushumiga (gross) masshtablaydi.
    Shunda Jami = kunlik/davr jami (masalan 231 mln) bilan mos keladi.
    """
    parts = [dict(r) for r in lists if isinstance(r, dict) and not r.get("is_total")]
    if not parts:
        if target_gross > 0:
            return [
                {
                    "id": "__all__",
                    "name": "Jami",
                    "qty": 0.0,
                    "checks": int(target_checks),
                    "revenue": float(target_gross),
                    "cost": 0.0,
                    "profit": 0.0,
                    "margin": 0.0,
                    "markup": 0.0,
                    "share": 100.0,
                    "is_total": True,
                    "scaled": True,
                }
            ]
        return lists

    sample_rev = sum(float(r.get("revenue") or 0) for r in parts)
    if sample_rev <= 0 or target_gross <= 0:
        return lists

    # Allaqachon to'liq qamrab olgan bo'lsa — masshtablash shart emas
    if abs(sample_rev - target_gross) / target_gross < 0.02 and sample_rev >= target_gross * 0.95:
        # Jami ni baribir to'liq gross ga tenglashtirish
        out = [r for r in lists if not r.get("is_total")]
        total_cost = sum(float(r.get("cost") or 0) for r in out)
        total_profit = sum(float(r.get("profit") or 0) for r in out)
        total_qty = sum(float(r.get("qty") or 0) for r in out)
        for r in out:
            r["share"] = (float(r["revenue"]) / target_gross * 100.0) if target_gross else 0.0
        out.append(
            {
                "id": "__all__",
                "name": "Jami",
                "qty": round(total_qty, 3),
                "checks": int(target_checks),
                "revenue": float(target_gross),
                "cost": total_cost,
                "profit": total_profit,
                "margin": (total_profit / target_gross * 100.0) if target_gross else 0.0,
                "markup": (total_profit / total_cost * 100.0) if total_cost > 0 else 0.0,
                "share": 100.0,
                "is_total": True,
                "scaled": False,
            }
        )
        return out

    scale = target_gross / sample_rev
    scaled: list[dict] = []
    for r in parts:
        share = float(r.get("revenue") or 0) / sample_rev
        rev = float(r["revenue"]) * scale
        cost = float(r.get("cost") or 0) * scale
        profit = rev - cost
        qty = float(r.get("qty") or 0) * scale
        checks_est = int(round(target_checks * share)) if target_checks and share > 0 else int(r.get("checks") or 0)
        scaled.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "qty": round(qty, 3),
                "checks": max(checks_est, 1) if rev > 0 else 0,
                "revenue": rev,
                "cost": cost,
                "profit": profit,
                "margin": (profit / rev * 100.0) if rev > 0 else 0.0,
                "markup": (profit / cost * 100.0) if cost > 0 else 0.0,
                "share": share * 100.0,
                "is_total": False,
                "scaled": True,
            }
        )

    total_cost = sum(float(r["cost"]) for r in scaled)
    total_profit = sum(float(r["profit"]) for r in scaled)
    total_qty = sum(float(r["qty"]) for r in scaled)
    scaled.append(
        {
            "id": "__all__",
            "name": "Jami",
            "qty": round(total_qty, 3),
            "checks": int(target_checks),
            "revenue": float(target_gross),
            "cost": total_cost,
            "profit": total_profit,
            "margin": (total_profit / target_gross * 100.0) if target_gross else 0.0,
            "markup": (total_profit / total_cost * 100.0) if total_cost > 0 else 0.0,
            "share": 100.0,
            "is_total": True,
            "scaled": True,
        }
    )
    return scaled


def _shift_dt(value) -> datetime | None:
    return _parse_dt(value)


def _normalize_api_shift(raw: dict, today: date) -> dict:
    """TezPOS smena obyektini bir xil formatga keltiradi."""
    opened = _shift_dt(
        raw.get("opened_at")
        or raw.get("opened_at_local")
        or raw.get("start_at")
        or raw.get("started_at")
        or raw.get("open_time")
        or raw.get("created_at")
    )
    closed = _shift_dt(
        raw.get("closed_at")
        or raw.get("closed_at_local")
        or raw.get("end_at")
        or raw.get("ended_at")
        or raw.get("close_time")
        or raw.get("finished_at")
    )
    status_raw = (raw.get("status") or "").strip().lower()
    is_open = status_raw in ("open", "opened", "active", "ochiq") or (
        not closed and bool(opened)
    )
    if not status_raw:
        status_raw = "open" if is_open else "closed"
    gross = _dec(
        raw.get("sales_total")
        or raw.get("total_sales")
        or raw.get("gross")
        or raw.get("total")
        or raw.get("revenue")
        or 0
    )
    checks = int(
        raw.get("sales_count")
        or raw.get("receipts_count")
        or raw.get("checks_count")
        or raw.get("orders_count")
        or 0
    )
    cashier = (
        raw.get("cashier_name")
        or raw.get("user_name")
        or raw.get("opened_by_name")
        or raw.get("employee_name")
        or ""
    )
    sid = str(raw.get("id") or raw.get("uuid") or "")
    return {
        "id": sid or (opened.isoformat() if opened else ""),
        "source": "api",
        "status": "open" if is_open else "closed",
        "status_label": "Ochiq" if is_open else "Yopilgan",
        "opened_at": opened.isoformat() if opened else "",
        "closed_at": closed.isoformat() if closed else "",
        "opened_display": timezone.localtime(opened).strftime("%d.%m.%Y %H:%M") if opened else "—",
        "closed_display": (
            timezone.localtime(closed).strftime("%d.%m.%Y %H:%M") if closed else ("Hozir" if is_open else "—")
        ),
        "cashier": (cashier or "").strip() or "Kassir",
        "opening_cash": float(_dec(raw.get("opening_cash") or raw.get("open_cash") or 0)),
        "closing_cash": float(_dec(raw.get("closing_cash") or raw.get("close_cash") or 0)),
        "gross": float(gross),
        "checks": checks,
        "profit": float(_dec(raw.get("profit") or 0)),
        "margin": float(_dec(raw.get("margin") or 0)),
        "sale_ids": [str(x) for x in (raw.get("sale_ids") or []) if x],
        "price_lists": [],
        "raw": raw,
    }


def _build_shifts_from_sales(
    sales: list[dict],
    *,
    today: date,
    gap_hours: float = 4.0,
    margin_ratio: Decimal = Decimal("0"),
) -> list[dict]:
    """
    Agar TezPOS /shifts API yo'q bo'lsa — sotuvlar oralig'idan smena yig'adi.
    Ketma-ket cheklar orasida gap_hours soatdan ko'p bo'lsa yangi smena.
    """
    timed: list[tuple[datetime, dict]] = []
    for s in sales:
        if not isinstance(s, dict):
            continue
        dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
        if not dt:
            continue
        timed.append((timezone.localtime(dt), s))
    timed.sort(key=lambda x: x[0])
    if not timed:
        return []

    gap = timedelta(hours=gap_hours)
    groups: list[list[tuple[datetime, dict]]] = [[timed[0]]]
    for dt, sale in timed[1:]:
        prev_dt = groups[-1][-1][0]
        if dt - prev_dt > gap:
            groups.append([(dt, sale)])
        else:
            groups[-1].append((dt, sale))

    now = timezone.localtime(timezone.now())
    out: list[dict] = []
    for idx, group in enumerate(reversed(groups), start=1):
        opened = group[0][0]
        closed = group[-1][0]
        # Oxirgi smena va oxirgi chek yaqin — ochiq deb hisoblash
        is_open = (now - closed) <= gap and closed.date() == today
        gross = sum((_dec(s.get("total")) for _, s in group), Decimal("0"))
        checks = len(group)
        profit = (gross * margin_ratio).quantize(Decimal("0.01")) if margin_ratio > 0 else Decimal("0")
        margin = float((profit / gross * 100) if gross > 0 else 0)
        sale_ids = [str(s.get("id")) for _, s in group if s.get("id")]
        out.append(
            {
                "id": f"session-{opened.strftime('%Y%m%d%H%M')}-{sale_ids[0][:8] if sale_ids else idx}",
                "source": "sales",
                "status": "open" if is_open else "closed",
                "status_label": "Ochiq" if is_open else "Yopilgan",
                "opened_at": opened.isoformat(),
                "closed_at": "" if is_open else closed.isoformat(),
                "opened_display": opened.strftime("%d.%m.%Y %H:%M"),
                "closed_display": "Hozir" if is_open else closed.strftime("%d.%m.%Y %H:%M"),
                "cashier": "Smena",
                "opening_cash": 0.0,
                "closing_cash": 0.0,
                "gross": float(gross),
                "checks": checks,
                "profit": float(profit),
                "margin": margin,
                "sale_ids": sale_ids,
                "price_lists": [],
            }
        )
    return out


def _enrich_shift_with_sales(
    shift: dict,
    sales: list[dict],
    *,
    margin_ratio: Decimal,
) -> dict:
    """API smenasiga mos sotuvlarni vaqt oralig'idan biriktiradi."""
    if shift.get("sale_ids"):
        ids = set(shift["sale_ids"])
        matched = [s for s in sales if str(s.get("id")) in ids]
    else:
        opened = _shift_dt(shift.get("opened_at"))
        closed = _shift_dt(shift.get("closed_at")) or timezone.now()
        matched = []
        if opened:
            for s in sales:
                dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
                if not dt:
                    continue
                if opened <= dt <= closed:
                    matched.append(s)
        shift["sale_ids"] = [str(s.get("id")) for s in matched if s.get("id")]
    if matched:
        gross = sum((_dec(s.get("total")) for s in matched), Decimal("0"))
        shift["gross"] = float(gross)
        shift["checks"] = len(matched)
        if not shift.get("profit"):
            profit = (gross * margin_ratio).quantize(Decimal("0.01")) if margin_ratio > 0 else Decimal("0")
            shift["profit"] = float(profit)
            shift["margin"] = float((profit / gross * 100) if gross > 0 else 0)
    return shift


def _build_price_list_stats_by_range(
    sales: list[dict],
    details: dict[str, dict],
    today: date,
    products_by_id: dict[str, SimpleNamespace],
    products_by_name: dict[str, SimpleNamespace],
    price_lists: list[dict],
    range_keys: list[str] | None = None,
) -> dict[str, list[dict]]:
    keys = range_keys or ["d1", "d7", "d15", "d30", "m1", "m3", "m6", "y1"]
    out: dict[str, list[dict]] = {}
    for key in keys:
        start, end = _range_window(key, today)
        ranged = []
        for s in sales:
            if not isinstance(s, dict):
                continue
            d = _sale_day(s)
            if not d or d < start or d > end:
                continue
            sid = str(s.get("id") or "")
            detail = details.get(sid)
            if detail:
                ranged.append(detail)
        out[key] = _aggregate_price_list_stats(
            ranged, products_by_id, products_by_name, price_lists
        )
    return out


def _refine_summaries_from_details(
    sales_stats: dict,
    sales: list[dict],
    details: dict[str, dict],
    today: date,
    products_by_id: dict,
    products_by_name: dict,
) -> None:
    """Agar chek detallari bo'lsa — foydani aniqroq hisoblaydi."""
    for key, pack in sales_stats.items():
        start, end = _range_window(key, today)
        profit = 0.0
        gross = 0.0
        used = 0
        total_in_range = 0
        for s in sales:
            if not isinstance(s, dict):
                continue
            d = _sale_day(s)
            if not d or d < start or d > end:
                continue
            total_in_range += 1
            total = float(_dec(s.get("total")))
            gross += total
            sid = str(s.get("id") or "")
            detail = details.get(sid)
            if not detail:
                continue
            used += 1
            cost, sale_profit = _estimate_sale_profit(
                detail, products_by_id, _dec(total), products_by_name
            )
            profit += float(sale_profit)
        summary = pack.get("summary") or {}
        if used > 0 and used >= max(1, int(total_in_range * 0.5)):
            # Detallar yetarli — aniq foyda; qolganlar uchun o'rtacha marja
            if used < total_in_range and profit > 0 and gross > 0:
                sample_gross = 0.0
                for s in sales:
                    if not isinstance(s, dict):
                        continue
                    d = _sale_day(s)
                    if not d or d < start or d > end:
                        continue
                    sid = str(s.get("id") or "")
                    if sid in details:
                        sample_gross += float(_dec(s.get("total")))
                ratio = (profit / sample_gross) if sample_gross > 0 else 0.0
                full_gross = float(summary.get("gross") or gross)
                profit = full_gross * ratio
                gross = full_gross
            else:
                gross = float(summary.get("gross") or gross)
            summary["profit"] = profit
            summary["margin"] = (profit / gross * 100.0) if gross > 0 else 0.0
            pack["summary"] = summary


def _fetch_sale_details(
    token: str,
    server: str,
    sale_ids: list[str],
    limit: int = 40,
    *,
    per_sale_timeout: float = 5.0,
    overall_timeout: float | None = None,
) -> dict[str, dict]:
    """Fetch sale receipts; never raises — timeouts/errors skip that sale."""
    out: dict[str, dict] = {}
    ids = [str(x) for x in sale_ids[:limit] if x]

    def one(sid: str):
        try:
            # get_sale default 5s — tez namuna uchun qisqaroq
            data = tezpos_api.api_request(
                "GET",
                f"/api/sales/{sid}/",
                token=token,
                server_name=server,
                timeout=per_sale_timeout,
            )
            return sid, data if isinstance(data, dict) else None
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            return sid, None
        except Exception:
            return sid, None

    if not ids:
        return out
    workers = min(10, max(4, len(ids)))
    deadline = time.time() + overall_timeout if overall_timeout else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, sid) for sid in ids]
        for fut in as_completed(futures):
            if deadline and time.time() > deadline:
                break
            try:
                sid, data = fut.result()
            except Exception:
                continue
            if data:
                out[sid] = data
    return out


def _fallback_sotuv_only_lists(
    gross: float,
    checks: int,
    margin_ratio: Decimal,
    price_lists: list[dict] | None = None,
) -> list[dict]:
    """Detallar bo‘lmasa — butun tushumni Sotuvga, Optom 0 (tez UI)."""
    if gross <= 0:
        return []
    ratio = float(margin_ratio or 0)
    if ratio <= 0:
        ratio = 0.25
    profit = gross * ratio
    cost = gross - profit
    out = [
        {
            "id": SELLING_LIST_ID,
            "name": "Sotuv",
            "qty": 0.0,
            "checks": int(checks),
            "revenue": float(gross),
            "cost": float(cost),
            "profit": float(profit),
            "margin": (profit / gross * 100.0) if gross else 0.0,
            "markup": (profit / cost * 100.0) if cost > 0 else 0.0,
            "share": 100.0,
            "is_total": False,
            "scaled": True,
            "estimated": True,
        }
    ]
    # Optom ro‘yxatlari bo‘lsa — 0 bilan emas, faqat Sotuv+Jami (UI faqat revenue>0)
    out.append(
        {
            "id": "__all__",
            "name": "Jami",
            "qty": 0.0,
            "checks": int(checks),
            "revenue": float(gross),
            "cost": float(cost),
            "profit": float(profit),
            "margin": (profit / gross * 100.0) if gross else 0.0,
            "markup": (profit / cost * 100.0) if cost > 0 else 0.0,
            "share": 100.0,
            "is_total": True,
            "scaled": True,
            "estimated": True,
        }
    )
    return out


def _catalog_margin_ratio(products: list) -> Decimal:
    """O'rtacha sof marja ulushi: faqat sotib olish narxi kiritilgan tovarlar."""
    ratios = []
    for p in products:
        selling = getattr(p, "selling_price", None) or Decimal("0")
        cost = getattr(p, "cost_price", None) or Decimal("0")
        if selling > 0 and cost > 0 and cost <= selling:
            ratios.append((selling - cost) / selling)
    if not ratios:
        return Decimal("0")
    return sum(ratios, Decimal("0")) / Decimal(len(ratios))


def _resolve_item_unit_cost(
    item: dict,
    products_by_id: dict[str, SimpleNamespace],
    products_by_name: dict[str, SimpleNamespace] | None = None,
) -> Decimal:
    """Sotib olish narxi; kiritilmagan bo'lsa 0 (foydaga qo'shilmaydi)."""
    unit_cost = _dec(
        item.get("unit_cost")
        or item.get("cost_price")
        or item.get("purchase_price")
        or item.get("buy_price")
    )
    if unit_cost > 0:
        return unit_cost
    p = _find_product(
        products_by_id,
        products_by_name,
        product_id=str(item.get("product_id") or item.get("product") or ""),
        product_name=item.get("product_name") or item.get("name") or "",
    )
    if p and (p.cost_price or Decimal("0")) > 0:
        return p.cost_price
    return Decimal("0")


def _estimate_sale_cost(
    sale_detail: dict,
    products_by_id: dict[str, SimpleNamespace],
    products_by_name: dict[str, SimpleNamespace] | None = None,
) -> Decimal:
    """Faqat sotib olish narxi bor qatorlar tannarxi."""
    cost = Decimal("0")
    for item in sale_detail.get("items") or []:
        unit_cost = _resolve_item_unit_cost(item, products_by_id, products_by_name)
        if unit_cost <= 0:
            continue
        cost += _dec(item.get("quantity")) * unit_cost
    return cost


def _estimate_sale_profit(
    sale_detail: dict,
    products_by_id: dict[str, SimpleNamespace],
    total: Decimal,
    products_by_name: dict[str, SimpleNamespace] | None = None,
    margin_ratio: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """
    Qaytaradi: (tannarx, foyda).
    Sotib olish narxi yo'q tovarlar tashlab ketiladi — ularning tushumi foydaga kirmaydi.
    """
    del total, margin_ratio  # chek jami / o'rtacha marja bilan taxmin qilinmaydi
    items = sale_detail.get("items") or []
    if not items:
        return Decimal("0"), Decimal("0")

    cost = Decimal("0")
    costed_rev = Decimal("0")
    for item in items:
        qty = _dec(item.get("quantity"))
        unit_price = _dec(item.get("unit_price"))
        line_total = _dec(item.get("total"), str(qty * unit_price))
        unit_cost = _resolve_item_unit_cost(item, products_by_id, products_by_name)
        if unit_cost <= 0:
            continue
        cost += qty * unit_cost
        costed_rev += line_total
    profit = (costed_rev - cost).quantize(Decimal("0.01"))
    return cost.quantize(Decimal("0.01")), profit


def _serialize_sale_payload(
    sale_detail: dict,
    cashier: str,
    products_by_id: dict,
    products_by_name: dict | None = None,
    margin_ratio: Decimal | None = None,
) -> dict:
    del margin_ratio
    items = []
    items_cost = Decimal("0")
    costed_rev = Decimal("0")
    for item in sale_detail.get("items") or []:
        qty = _dec(item.get("quantity"))
        unit_price = _dec(item.get("unit_price"))
        p = _find_product(
            products_by_id,
            products_by_name,
            product_id=str(item.get("product_id") or ""),
            product_name=item.get("product_name") or "",
        )
        unit_cost = _resolve_item_unit_cost(item, products_by_id, products_by_name)
        line_total = _dec(item.get("total"), str(qty * unit_price))
        line_cost = qty * unit_cost if unit_cost > 0 else Decimal("0")
        if unit_cost > 0:
            items_cost += line_cost
            costed_rev += line_total
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
    profit = (costed_rev - items_cost).quantize(Decimal("0.01"))
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
        "quantity": str(_dec(request.POST.get("stock_qty"))),
        "min_stock": str(_dec(request.POST.get("min_stock"))),
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


def _product_payload_from_import_row(row: dict) -> tuple[dict | None, str | None]:
    """Import qatoridan TezPOS create payload."""
    name = str(row.get("name") or "").strip()
    if not name:
        return None, "Mahsulot nomi bo‘sh"

    barcode = str(row.get("barcode") or "").strip()
    codes = [c.strip() for c in (row.get("barcodes") or []) if str(c).strip()]
    if barcode and barcode not in codes:
        codes.insert(0, barcode)

    selling = _dec(row.get("selling_price") or row.get("price"))
    cost = _dec(row.get("cost_price"))
    qty = _dec(row.get("stock_qty") or row.get("quantity"))
    min_stock = _dec(row.get("min_stock"), "0")
    unit = str(row.get("unit") or "dona").strip() or "dona"

    payload: dict = {
        "name": name,
        "unit": unit,
        "price": str(selling),
        "cost_price": str(cost),
        "quantity": str(qty),
        "min_stock": str(min_stock),
        "barcode": barcode or (codes[0] if codes else ""),
        "barcodes": codes or ([barcode] if barcode else []),
        "is_active": True,
    }

    category = str(row.get("category") or "").strip()
    brand = str(row.get("brand") or "").strip()
    if category:
        payload["category"] = category
    if brand:
        payload["brand"] = brand

    list_prices_in = row.get("list_prices")
    list_prices: dict[str, str] = {}
    if isinstance(list_prices_in, dict):
        for k, v in list_prices_in.items():
            key = str(k).strip()
            if not key:
                continue
            list_prices[key] = str(_dec(v))
    # pl_<id> kalitlari
    for k, v in row.items():
        if not str(k).startswith("pl_"):
            continue
        lid = str(k)[3:].strip()
        if lid:
            list_prices[lid] = str(_dec(v))
    if list_prices:
        payload["list_prices"] = list_prices

    return payload, None


@login_required
@require_POST
def cabinet_products_import(request):
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    rows = body.get("products") or []
    if not isinstance(rows, list) or not rows:
        return JsonResponse({"error": "Mahsulotlar ro‘yxati bo‘sh"}, status=400)
    if len(rows) > 5000:
        return JsonResponse({"error": "Bir martada eng ko‘pi 5000 ta mahsulot"}, status=400)

    created = 0
    failed: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            failed.append({"row": idx, "error": "Noto‘g‘ri qator"})
            continue
        payload, err = _product_payload_from_import_row(row)
        if err or not payload:
            failed.append({"row": idx, "name": str(row.get("name") or ""), "error": err or "Xato"})
            continue
        try:
            tezpos_api.create_product(token, server, payload)
            created += 1
        except tezpos_api.TezPosApiError as exc:
            failed.append({"row": idx, "name": payload.get("name", ""), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"row": idx, "name": payload.get("name", ""), "error": str(exc)})

    return JsonResponse(
        {
            "ok": True,
            "created": created,
            "failed": failed,
            "total": len(rows),
        }
    )


_EXPORT_FIELD_LABELS = {
    "name": "Mahsulot nomi",
    "barcode": "Shtrixkod (barkod)",
    "selling_price": "Sotuv narxi",
    "cost_price": "Sotib olish narxi",
    "stock_qty": "Omborda qoldiq",
    "unit": "O‘lchov birligi",
    "category": "Bo‘lim",
    "brand": "Brend",
    "min_stock": "Minimal qoldiq",
}


def _export_cell_value(product: SimpleNamespace, key: str, price_lists_by_id: dict) -> str | float | int:
    list_prices = getattr(product, "list_prices", None) or {}
    if key.startswith("pl_"):
        pid = key[3:]
        return float(list_prices.get(pid) or list_prices.get(str(pid)) or 0)
    if key == "name":
        return product.name or ""
    if key == "barcode":
        return product.barcode or ""
    if key == "selling_price":
        return float(product.selling_price or 0)
    if key == "cost_price":
        return float(product.cost_price or 0)
    if key == "stock_qty":
        return float(product.stock_qty or 0)
    if key == "unit":
        return product.unit or "dona"
    if key == "category":
        return product.category or ""
    if key == "brand":
        return product.brand or ""
    if key == "min_stock":
        return float(product.min_stock or 0)
    return ""


@login_required
@require_GET
def cabinet_products_export(request):
    """Barcha mahsulotlarni Excel (.xlsx) — ustunlar avtomatik kenglikda."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]

    raw_fields = [
        x.strip()
        for x in (request.GET.get("fields") or "name,barcode,cost_price,selling_price").split(",")
        if x.strip()
    ]
    if "name" not in raw_fields:
        raw_fields.insert(0, "name")
    # Takrorlarni olib tashlash, tartibni saqlash
    fields: list[str] = []
    seen = set()
    for f in raw_fields:
        if f in seen:
            continue
        seen.add(f)
        fields.append(f)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_p = pool.submit(
                lambda: tezpos_api.get_products(token, server, max_pages=80)
            )
            fut_pl = pool.submit(
                lambda: tezpos_api.get_price_lists(token, server) or []
            )
            products_raw = fut_p.result() or []
            price_lists = fut_pl.result() or []
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    price_lists_by_id = {
        str(pl.get("id")): pl
        for pl in price_lists
        if isinstance(pl, dict) and pl.get("id")
    }

    headers = []
    for key in fields:
        if key.startswith("pl_"):
            pl = price_lists_by_id.get(key[3:]) or {}
            headers.append(f"Narxlar: {pl.get('name') or key[3:]}")
        else:
            headers.append(_EXPORT_FIELD_LABELS.get(key, key))

    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return JsonResponse({"error": "openpyxl o‘rnatilmagan"}, status=500)

    wb = Workbook()
    ws = wb.active
    ws.title = "Mahsulotlar"
    ws.append(headers)
    for p in products:
        row = [_export_cell_value(p, key, price_lists_by_id) for key in fields]
        ws.append(row)

    # Ustun kengligi — nomlar kesilmasin
    for col_idx, header in enumerate(headers, start=1):
        max_len = len(str(header))
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=2):
            val = row[0].value
            if val is None:
                continue
            max_len = max(max_len, len(str(val)))
        width = min(80, max(14, max_len + 2))
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    filename = "barcha_mahsulotlar.xlsx"
    resp = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "no-store"
    return resp


def _cabinet_shell_context(request, tenant, section, *, form=None, sale_date=None):
    """Engil bo‘limlar (bot, qarzdorlar) — og‘ir API siz tezkor ochilish."""
    sale_date = sale_date or timezone.localdate()
    empty_chart = {"labels": [], "totals": [], "counts": []}
    return {
        "tenant": tenant,
        "form": form or _empty_form(),
        "products": [],
        "products_json": "[]",
        "price_lists_json": "[]",
        "shifts_json": "[]",
        "shifts_source": "none",
        "brand_choices": [],
        "category_choices": [],
        "all_products_count": 0,
        "form_errors": None,
        "section": section,
        "total_sales": 0,
        "gross": Decimal("0"),
        "cost": Decimal("0"),
        "profit": Decimal("0"),
        "margin": Decimal("0"),
        "today_count": 0,
        "today_gross": Decimal("0"),
        "today_profit": Decimal("0"),
        "range_checks": 0,
        "price_list_stats_json": "{}",
        "recent_sales": [],
        "sale_date": sale_date,
        "sale_date_iso": sale_date.isoformat(),
        "day_sales_count": 0,
        "day_gross": Decimal("0"),
        "day_cost_total": Decimal("0"),
        "day_profit_total": Decimal("0"),
        "day_pay_totals": {},
        "day_sales_json": "[]",
        "cashier_name": _cashier_name(request),
        "low_stock": [],
        "signals": [],
        "active_signals_count": 0,
        "near_min_json": "[]",
        "abc_rows": [],
        "abc_matrix": {},
        "abc_total": 0,
        "top_products_json": "[]",
        "top_customers_json": "[]",
        "chart_labels_json": "[]",
        "chart_totals_json": "[]",
        "chart_counts_json": "[]",
        "sales_stats_json": "{}",
        "share_items_json": "[]",
        "bot_settings": {
            "enabled": bool(tenant.telegram_enabled),
            "token": tenant.telegram_bot_token or "",
            "token_set": bool(tenant.telegram_bot_token),
            "recipients": tenant.telegram_recipients or "",
            "notify_open": bool(tenant.telegram_notify_open),
            "notify_close": bool(tenant.telegram_notify_close),
        },
        "bot_settings_json": json.dumps(
            {
                "enabled": bool(tenant.telegram_enabled),
                "token_set": bool(tenant.telegram_bot_token),
                "notify_open": bool(tenant.telegram_notify_open),
                "notify_close": bool(tenant.telegram_notify_close),
            },
            ensure_ascii=False,
        ),
        "report_charts_json": json.dumps(
            {
                "daily": empty_chart,
                "weekly": empty_chart,
                "monthly": empty_chart,
            },
            ensure_ascii=False,
        ),
        "fast_nav": True,
    }


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

    section = section_force or request.GET.get("section", "overview")
    allowed = {
        "overview",
        "sales",
        "inventory",
        "products",
        "stock_value",
        "reports",
        "abc",
        "signals",
        "labels",
        "tops",
        "shifts",
        "bot",
        "debtors",
    }
    if section not in allowed:
        section = "overview"

    # Engil bo‘limlar — TezPOS API kutmasdan darhol ochiladi (AJAX o‘zi yuklaydi)
    FAST_SHELL = {
        "overview",
        "tops",
        "bot",
        "debtors",
        "products",
        "inventory",
        "stock_value",
        "labels",
        "signals",
        "sales",
        "shifts",
        "reports",
        "abc",
    }
    if section in FAST_SHELL and request.method != "POST":
        ctx = _cabinet_shell_context(
            request, tenant, section, form=form, sale_date=sale_date
        )
        ctx["fast_nav"] = True
        resp = render(request, "accounts/cabinet.html", ctx)
        resp["Cache-Control"] = "private, no-cache"
        return resp

    api_warnings = []
    today = timezone.localdate()
    raw_products: list = []
    raw_sales: list = []
    day_sales_raw: list = []

    # Og‘ir SSR faqat kerakli joylarda
    need_products = section in (
        "products",
        "inventory",
        "stock_value",
        "labels",
        "signals",
        "abc",
    )
    need_chart_sales = section in ("abc", "reports")
    need_day_sales = section == "sales"
    # Chek detali SSR da emas — jadval tez ochilsin (foyda taxminiy)
    need_sale_details = False
    need_price_list_stats = False
    need_price_lists = section in (
        "products",
        "inventory",
        "stock_value",
    )
    need_top_stats = section == "abc"
    need_shifts = section == "shifts"
    # all=true ko‘pincha 1 so‘rov — ortiqcha sahifa kutmaslik
    if section in ("products", "inventory", "stock_value", "labels"):
        product_pages = 4
    elif section in ("signals", "sales", "abc"):
        product_pages = 2
    else:
        product_pages = 1

    def _load_products():
        key = f"products:{server}:{product_pages}"
        return _memo_get(
            key,
            90.0,
            lambda: tezpos_api.get_products(token, server, max_pages=product_pages),
        )

    def _load_chart_sales():
        if section == "reports":
            days, pages, timeout = 30, 3, 20
        elif section == "abc":
            days, pages, timeout = 14, 3, 18
        else:
            days, pages, timeout = 7, 2, 16
        key = f"sales:{server}:{days}:{pages}"

        def _loader():
            try:
                return tezpos_api.get_sales(
                    token,
                    server,
                    date_from=(today - timedelta(days=days)).isoformat(),
                    date_to=today.isoformat(),
                    timeout=timeout,
                    max_pages=pages,
                )
            except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
                if getattr(exc, "status", None) in (401, 403):
                    raise
                return tezpos_api.get_sales(
                    token,
                    server,
                    date_from=(today - timedelta(days=2)).isoformat(),
                    date_to=today.isoformat(),
                    timeout=12,
                    max_pages=2,
                )

        return _memo_get(key, 60.0, _loader)

    def _load_price_lists():
        key = f"price_lists:{server}"

        def _loader():
            try:
                return tezpos_api.get_price_lists(token, server)
            except (tezpos_api.TezPosApiError, TimeoutError, OSError):
                return []

        return _memo_get(key, 120.0, _loader)

    def _load_shifts_api():
        key = f"shifts:{server}:{today.isoformat()}"

        def _loader():
            try:
                return tezpos_api.get_shifts(
                    token,
                    server,
                    date_from=(today - timedelta(days=14)).isoformat(),
                    date_to=today.isoformat(),
                    timeout=10,
                    max_pages=2,
                )
            except (tezpos_api.TezPosApiError, TimeoutError, OSError):
                return []

        return _memo_get(key, 45.0, _loader)
    def _auth_fail(exc):
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            messages.error(request, "Sessiya tugadi. Qayta kiring.")
            return True
        return False

    raw_price_lists: list = []
    raw_shifts_api: list = []
    # Mahsulotlar + grafik sotuvlari + kunlik sotuvlar + narxlar ro'yxati parallel
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            fut_p = pool.submit(_load_products) if need_products else None
            fut_s = pool.submit(_load_chart_sales) if need_chart_sales else None
            fut_d = (
                pool.submit(tezpos_api.get_sales_for_day, token, server, sale_date.isoformat())
                if need_day_sales
                else None
            )
            fut_pl = pool.submit(_load_price_lists) if need_price_lists else None
            fut_sh = pool.submit(_load_shifts_api) if need_shifts else None
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
            if fut_d is not None:
                try:
                    day_sales_raw = fut_d.result()
                except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
                    if _auth_fail(exc):
                        return redirect("login")
                    day_sales_raw = [
                        s
                        for s in raw_sales
                        if isinstance(s, dict) and _sale_day(s) == sale_date
                    ]
            if fut_pl is not None:
                raw_price_lists = fut_pl.result() or []
            if fut_sh is not None:
                raw_shifts_api = fut_sh.result() or []
    except Exception as exc:
        api_warnings.append(str(exc))

    if need_day_sales and not day_sales_raw:
        # Bugun sotuv yo'q — oxirgi sotuv kuniga (faqat default sana)
        if not request.GET.get("sale_date") and section in ("overview", "sales"):
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
        clean = []
        for w in api_warnings[:2]:
            s = " ".join(str(w).split())
            if "<!doctype" in s.lower() or "<html" in s.lower():
                s = "TezPOS API dan ma’lumot olinmadi (noto‘g‘ri API manzili yoki endpoint)."
            clean.append(s[:180])
        messages.warning(request, " · ".join(clean))

    products = [_map_product(p) for p in raw_products if isinstance(p, dict)]
    # Faol mahsulotlarni birinchi ko'rsatish
    products.sort(key=lambda p: (not p.is_active, p.name.lower()))
    products_by_id = {str(p.id): p for p in products}
    products_by_name = _products_by_name(products)
    price_lists = [pl for pl in raw_price_lists if isinstance(pl, dict) and pl.get("is_active", True)]

    brand_choices = sorted({p.brand for p in products if p.brand})
    category_choices = sorted({p.category for p in products if p.category})

    day_sales_raw = [s for s in day_sales_raw if isinstance(s, dict)]
    day_sales_raw.sort(
        key=lambda s: _parse_dt(s.get("completed_at") or s.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Detail — faqat kunlik sotuvlar (limit bilan, tez ochilishi uchun)
    need_detail_ids = []
    detail_limit = 30
    if need_sale_details:
        need_detail_ids = [str(s.get("id")) for s in day_sales_raw if s.get("id")]
        detail_limit = min(max(len(need_detail_ids), 1), 35)
    details = (
        _fetch_sale_details(token, server, need_detail_ids, limit=detail_limit)
        if need_detail_ids
        else {}
    )
    cashier = _cashier_name(request)
    margin_ratio = _catalog_margin_ratio(products)

    day_sales_list = []
    day_sales_payload = []
    day_gross = Decimal("0")
    day_cost_total = Decimal("0")
    day_pay_totals = defaultdict(Decimal)
    for s in day_sales_raw:
        sid = str(s.get("id"))
        detail = details.get(sid) or s
        total = _dec(detail.get("total") or s.get("total"))
        cost, profit = _estimate_sale_profit(
            detail, products_by_id, total, products_by_name, margin_ratio
        )
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
                products_by_name,
                margin_ratio,
            )
        )

    day_profit_total = day_gross - day_cost_total

    # Tannarx topilmagan cheklar — katalog marjasi (foyda = o'rtadagi summa)
    if day_gross > 0 and margin_ratio > 0:
        for i, row in enumerate(day_sales_list):
            if row.total_amount > 0 and row.total_cost <= 0:
                row.profit = (row.total_amount * margin_ratio).quantize(Decimal("0.01"))
                row.total_cost = row.total_amount - row.profit
                if i < len(day_sales_payload):
                    day_sales_payload[i]["profit"] = float(row.profit)
                    day_sales_payload[i]["total_cost"] = float(row.total_cost)
        day_cost_total = sum((r.total_cost for r in day_sales_list), Decimal("0"))
        day_profit_total = day_gross - day_cost_total

    if section == "sales" and request.GET.get("export") == "excel":
        return _export_daily_sales_excel(day_sales_payload, sale_date, cashier)

    # Top mahsulotlar — API stats; bo'sh bo'lsa oxirgi cheklar detali
    product_qty = defaultdict(Decimal)
    product_rev = defaultdict(Decimal)
    product_meta: dict[str, dict] = {}
    item_rows = []
    top_payload = {"items": []}
    if need_top_stats:
        try:
            top_payload = tezpos_api.get_top_products(token, server, days=30, limit=100)
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            top_payload = {"items": []}

    for row in top_payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or row.get("id") or "")
        if not pid:
            continue
        qty = _dec(row.get("quantity") or row.get("qty") or row.get("sold_qty"))
        rev = _dec(
            row.get("revenue")
            or row.get("total")
            or row.get("amount")
            or row.get("sum")
        )
        p = products_by_id.get(pid)
        if rev <= 0 and qty > 0:
            unit = _dec(row.get("unit_price") or row.get("price"))
            if unit <= 0 and p:
                unit = p.selling_price
            rev = qty * unit if unit > 0 else Decimal("0")
        if rev <= 0 and p and qty > 0:
            rev = qty * p.selling_price
        if rev <= 0 and qty <= 0:
            continue
        product_qty[pid] = qty
        product_rev[pid] = rev
        name = (
            (row.get("product_name") or row.get("name") or "")
            or (p.name if p else "")
            or f"Mahsulot {pid[:8]}"
        )
        product_meta[pid] = {
            "name": name,
            "image": (p.display_image if p else "") or str(row.get("image") or ""),
            "stock": float(p.stock_qty) if p else float(_dec(row.get("stock") or row.get("quantity_left"))),
            "wholesale": float(p.wholesale_price or p.cost_price) if p else float(_dec(row.get("wholesale_price"))),
            "selling": float(p.selling_price) if p else float(_dec(row.get("selling_price") or row.get("unit_price"))),
        }
        item_rows.append(
            {
                "product_id": pid,
                "quantity": qty,
                "unit_price": (rev / qty) if qty else Decimal("0"),
                "day": today,
                "name": name,
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
            if len(recent_ids) >= 40:
                break
        if recent_ids:
            details.update(_fetch_sale_details(token, server, recent_ids, limit=40))
        for detail in details.values():
            d = _sale_day(detail)
            for item in detail.get("items") or []:
                pid = str(item.get("product_id") or "")
                qty = _dec(item.get("quantity"))
                unit_price = _dec(item.get("unit_price"))
                name = (item.get("product_name") or item.get("name") or "").strip()
                if not pid:
                    if not name:
                        continue
                    pid = f"name:{name.casefold()}"
                line_rev = qty * unit_price
                product_qty[pid] += qty
                product_rev[pid] += line_rev
                p = products_by_id.get(pid) if not pid.startswith("name:") else None
                if not p and name:
                    p = _find_product(products_by_id, products_by_name, product_name=name)
                meta = product_meta.get(pid) or {}
                product_meta[pid] = {
                    "name": name or meta.get("name") or (p.name if p else "Mahsulot"),
                    "image": (p.display_image if p else "") or meta.get("image") or "",
                    "stock": float(p.stock_qty) if p else float(meta.get("stock") or 0),
                    "wholesale": float(p.wholesale_price or p.cost_price)
                    if p
                    else float(meta.get("wholesale") or 0),
                    "selling": float(p.selling_price)
                    if p
                    else float(meta.get("selling") or unit_price),
                }
                item_rows.append(
                    {
                        "product_id": pid,
                        "quantity": qty,
                        "unit_price": unit_price,
                        "day": d,
                        "name": product_meta[pid]["name"],
                    }
                )

    sales_stats = _build_charts_from_sales(
        [s for s in raw_sales if isinstance(s, dict)], today
    )
    margin_ratio = _catalog_margin_ratio(products)
    _attach_range_summaries(
        sales_stats,
        [s for s in raw_sales if isinstance(s, dict)],
        today,
        margin_ratio,
    )
    if details:
        _refine_summaries_from_details(
            sales_stats,
            [s for s in raw_sales if isinstance(s, dict)],
            details,
            today,
            products_by_id,
            products_by_name,
        )
    price_list_stats = (
        _build_price_list_stats_by_range(
            [s for s in raw_sales if isinstance(s, dict)],
            details,
            today,
            products_by_id,
            products_by_name,
            price_lists,
            range_keys=["d1", "d7"],
        )
        if need_price_list_stats
        else {}
    )
    chart_labels = sales_stats["d7"]["labels"]
    chart_totals = sales_stats["d7"]["totals"]
    chart_counts = sales_stats["d7"]["counts"]
    d_pack, w_pack, m_pack = sales_stats["d7"], sales_stats["m3"], sales_stats["m6"]

    # Default KPI — tanlangan davr (7 kun)
    d7_summary = (sales_stats.get("d7") or {}).get("summary") or {}
    gross = sum((_dec(s.get("total")) for s in raw_sales if isinstance(s, dict)), Decimal("0"))
    range_checks = int(d7_summary.get("checks") or 0)
    range_gross = Decimal(str(d7_summary.get("gross") or 0))
    range_profit = Decimal(str(d7_summary.get("profit") or 0))
    range_margin = Decimal(str(d7_summary.get("margin") or 0))

    # Bugungi ko'rsatkichlar + sof foyda (sotuv - tannarx)
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
        today_profit = today_gross  # detail yo'q bo'lsa taxminiy
        cost = day_cost_total
        profit = day_profit_total

    # Overview KPI: tanlangan davr (7 kun default)
    if section == "overview":
        if range_checks > 0 or range_gross > 0:
            profit = range_profit
            cost = range_gross - range_profit
            margin = range_margin
            gross = range_gross
        else:
            margin = Decimal("0")
            profit = Decimal("0")
            cost = Decimal("0")
    else:
        margin = (profit / day_gross * 100) if day_gross > 0 else Decimal("0")
        if not day_sales_list:
            cost = Decimal("0")
            profit = Decimal("0")
            margin = Decimal("0")
            day_profit_total = Decimal("0")
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
        meta = product_meta.get(pid) or {}
        if not p and not meta:
            continue
        top_products.append(
            {
                "name": (p.name if p else "") or meta.get("name") or "Mahsulot",
                "image": (p.display_image if p else "") or meta.get("image") or "",
                "qty": float(product_qty[pid]),
                "revenue": float(rev),
                "stock": float(p.stock_qty) if p else float(meta.get("stock") or 0),
                "wholesale": float(p.wholesale_price or p.cost_price)
                if p
                else float(meta.get("wholesale") or 0),
                "selling": float(p.selling_price) if p else float(meta.get("selling") or 0),
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
            "list_prices": {
                str(k): float(v) for k, v in (p.list_prices or {}).items()
            },
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

    price_lists_payload = [
        {
            "id": str(pl.get("id") or ""),
            "name": (pl.get("name") or "").strip() or "Narxlar",
            "is_selling": bool(pl.get("is_selling")),
        }
        for pl in price_lists
        if str(pl.get("id") or "")
    ]

    # Smenalar — TezPOS API yoki sotuvlardan
    shifts_payload: list[dict] = []
    shifts_source = "none"
    if need_shifts:
        sales_for_shifts = [s for s in raw_sales if isinstance(s, dict)]
        if raw_shifts_api:
            shifts_source = "api"
            for raw in raw_shifts_api:
                if not isinstance(raw, dict):
                    continue
                sh = _normalize_api_shift(raw, today)
                sh = _enrich_shift_with_sales(
                    sh, sales_for_shifts, margin_ratio=margin_ratio
                )
                sh.pop("raw", None)
                shifts_payload.append(sh)
        if not shifts_payload:
            shifts_source = "sales"
            shifts_payload = _build_shifts_from_sales(
                sales_for_shifts,
                today=today,
                gap_hours=4.0,
                margin_ratio=margin_ratio,
            )
        # Narxlar ro‘yxati AJAX (shift-detail) orqali — SSR da 8×detail sekin

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
        "stock_value",
        "reports",
        "abc",
        "signals",
        "labels",
        "tops",
        "shifts",
        "bot",
        "debtors",
    }
    if section not in allowed:
        section = "overview"

    ctx = {
            "tenant": tenant,
            "form": form,
            "products": products,
            "products_json": json.dumps(products_payload, ensure_ascii=False),
            "price_lists_json": json.dumps(price_lists_payload, ensure_ascii=False),
            "shifts_json": json.dumps(shifts_payload, ensure_ascii=False),
            "shifts_source": shifts_source,
            "brand_choices": brand_choices,
            "category_choices": category_choices,
            "all_products_count": len(products),
            "form_errors": form.errors if request.method == "POST" else None,
            "section": section,
            "total_sales": range_checks if section == "overview" else len(raw_sales),
            "gross": gross,
            "cost": cost,
            "profit": profit,
            "margin": margin,
            "today_count": today_count,
            "today_gross": today_gross,
            "today_profit": today_profit,
            "range_checks": range_checks,
            "price_list_stats_json": json.dumps(price_list_stats, ensure_ascii=False),
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
            "bot_settings": {
                "enabled": bool(tenant.telegram_enabled),
                "token": tenant.telegram_bot_token or "",
                "token_set": bool(tenant.telegram_bot_token),
                "recipients": tenant.telegram_recipients or "",
                "notify_open": bool(tenant.telegram_notify_open),
                "notify_close": bool(tenant.telegram_notify_close),
            },
            "bot_settings_json": json.dumps(
                {
                    "enabled": bool(tenant.telegram_enabled),
                    "token_set": bool(tenant.telegram_bot_token),
                    "notify_open": bool(tenant.telegram_notify_open),
                    "notify_close": bool(tenant.telegram_notify_close),
                },
                ensure_ascii=False,
            ),
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
            "fast_nav": True,
        }
    resp = render(request, "accounts/cabinet.html", ctx)
    # Brauzer navigatsiyasini biroz tezlatish (shaxsiy kabinet — kesh emas)
    resp["Cache-Control"] = "private, no-cache"
    return resp


@login_required
@require_GET
def cabinet_range_stats(request):
    """Tanlangan kun/oraliq: grafik + KPI + narxlar ro'yxati (jonli AJAX)."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    today = timezone.localdate()

    custom_from = _parse_iso_date(request.GET.get("from"))
    custom_to = _parse_iso_date(request.GET.get("to"))
    range_key = (request.GET.get("range") or "").strip()

    if custom_from and custom_to:
        start, end = custom_from, custom_to
        if end < start:
            start, end = end, start
        # Max 2 yil
        if (end - start).days > 730:
            start = end - timedelta(days=730)
        range_key = f"custom:{start.isoformat()}:{end.isoformat()}"
    else:
        allowed = {"d1", "d7", "d15", "d30", "m1", "m3", "m6", "y1"}
        if range_key not in allowed:
            range_key = "d7"
        start, end = _range_window(range_key, today)

    span = (end - start).days + 1
    fast = (request.GET.get("fast") or "").strip() in ("1", "true", "yes")
    # Contabo WAN: to‘liq detail emas — kichik namuna + scale (Sotuv/Optom tez)
    if fast or span <= 1:
        max_pages, product_pages, sales_timeout = 1, 4, 10
        detail_cap, detail_each, detail_budget = 10, 3.5, 7.0
    elif span <= 7:
        max_pages, product_pages, sales_timeout = 1, 4, 12
        detail_cap, detail_each, detail_budget = 14, 4.0, 9.0
    elif span <= 31:
        max_pages, product_pages, sales_timeout = 2, 4, 16
        detail_cap, detail_each, detail_budget = 18, 4.0, 11.0
    else:
        max_pages, product_pages, sales_timeout = 2, 3, 18
        detail_cap, detail_each, detail_budget = 16, 4.0, 10.0

    memo_prefix = f"{server}|{(token or '')[-12:]}"
    sales: list = []
    products_raw: list = []
    price_lists: list = []
    api_err = ""

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_s = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|sales|{start}|{end}|{max_pages}",
                    60.0,
                    lambda: tezpos_api.get_sales(
                        token,
                        server,
                        date_from=start.isoformat(),
                        date_to=end.isoformat(),
                        timeout=sales_timeout,
                        max_pages=max_pages,
                    ),
                )
            )
            fut_p = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|products|{product_pages}",
                    600.0,
                    lambda: tezpos_api.get_products(
                        token, server, max_pages=product_pages
                    ),
                )
            )
            fut_pl = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|price_lists",
                    600.0,
                    lambda: tezpos_api.get_price_lists(token, server) or [],
                )
            )
            sales = fut_s.result() or []
            products_raw = fut_p.result() or []
            price_lists = fut_pl.result() or []
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        api_err = str(exc)
    except (TimeoutError, OSError) as exc:
        api_err = str(exc)

    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    products_by_id = {str(p.id): p for p in products}
    products_by_name = _products_by_name(products)
    margin_ratio = _catalog_margin_ratio(products) if products else Decimal("0.25")
    price_lists = [
        pl for pl in price_lists if isinstance(pl, dict) and pl.get("is_active", True)
    ]

    sales = [s for s in sales if isinstance(s, dict)]
    sales.sort(
        key=lambda s: _parse_dt(s.get("completed_at") or s.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    sales = [
        s
        for s in sales
        if (d := _sale_day(s)) is not None and start <= d <= end
    ]
    chart = _chart_pack_for_dates(sales, start, end)
    checks = len(sales)
    gross = float(sum((_dec(s.get("total")) for s in sales), Decimal("0")))
    profit = gross * float(margin_ratio) if margin_ratio else gross * 0.25
    margin = (profit / gross * 100.0) if gross > 0 else 0.0

    lists: list = []
    details_used = 0
    if gross > 0:
        # Allaqachon items bor cheklar (list endpoint nested bersa)
        embedded = [
            s for s in sales if isinstance(s.get("items"), list) and s.get("items")
        ]
        details: dict[str, dict] = {
            str(s.get("id")): s for s in embedded if s.get("id")
        }
        need_ids = [
            str(s.get("id"))
            for s in sales
            if s.get("id") and str(s.get("id")) not in details
        ]
        # Namuna: oxirgi cheklar (eng yangi — allaqachon sort desc)
        sample_n = min(detail_cap, len(need_ids))
        if sample_n > 0:
            fetched = _fetch_sale_details(
                token,
                server,
                need_ids[:sample_n],
                limit=sample_n,
                per_sale_timeout=detail_each,
                overall_timeout=detail_budget,
            )
            details.update(fetched)
        details_used = len(details)
        if details:
            lists = _aggregate_price_list_stats(
                list(details.values()),
                products_by_id,
                products_by_name,
                price_lists,
            )
            lists = _scale_price_list_stats(lists, gross, checks)
            jami = next((r for r in lists if r.get("is_total")), None)
            if jami and float(jami.get("revenue") or 0) > 0:
                profit = float(jami.get("profit") or profit)
                margin = float(jami.get("margin") or margin)
        else:
            lists = _fallback_sotuv_only_lists(gross, checks, margin_ratio, price_lists)
            jami = lists[-1] if lists else None
            if jami:
                profit = float(jami.get("profit") or profit)
                margin = float(jami.get("margin") or margin)

    payload = {
        "range": range_key,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "summary": {
            "checks": checks,
            "gross": gross,
            "profit": profit,
            "margin": margin,
            "details_used": details_used,
        },
        "chart": chart,
        "priceLists": lists,
        "partial": details_used < max(1, min(checks, detail_cap)),
        "fast": bool(fast),
    }
    if api_err:
        payload["error"] = api_err
    return JsonResponse(payload)


def _top_products_from_details(
    details: dict,
    products_by_id: dict,
    products_by_name: dict,
    limit: int = 100,
) -> list[dict]:
    product_qty: dict[str, Decimal] = defaultdict(Decimal)
    product_rev: dict[str, Decimal] = defaultdict(Decimal)
    product_meta: dict[str, dict] = {}
    for detail in details.values():
        for item in detail.get("items") or []:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("product_id") or "")
            qty = _dec(item.get("quantity"))
            unit_price = _dec(item.get("unit_price") or item.get("price"))
            name = (item.get("product_name") or item.get("name") or "").strip()
            if not pid:
                if not name:
                    continue
                pid = f"name:{name.casefold()}"
            line_rev = qty * unit_price
            if qty <= 0 and line_rev <= 0:
                continue
            product_qty[pid] += qty
            product_rev[pid] += line_rev
            p = products_by_id.get(pid) if not pid.startswith("name:") else None
            if not p and name:
                p = _find_product(products_by_id, products_by_name, product_name=name)
            meta = product_meta.get(pid) or {}
            product_meta[pid] = {
                "name": name or meta.get("name") or (p.name if p else "Mahsulot"),
                "image": (p.display_image if p else "") or meta.get("image") or "",
                "stock": float(p.stock_qty) if p else float(meta.get("stock") or 0),
                "wholesale": float(p.wholesale_price or p.cost_price)
                if p
                else float(meta.get("wholesale") or 0),
                "selling": float(p.selling_price)
                if p
                else float(meta.get("selling") or unit_price),
            }
    out = []
    for pid, rev in sorted(product_rev.items(), key=lambda x: x[1], reverse=True)[:limit]:
        meta = product_meta.get(pid) or {}
        p = products_by_id.get(pid)
        out.append(
            {
                "name": (p.name if p else "") or meta.get("name") or "Mahsulot",
                "image": (p.display_image if p else "") or meta.get("image") or "",
                "qty": float(product_qty[pid]),
                "revenue": float(rev),
                "stock": float(p.stock_qty) if p else float(meta.get("stock") or 0),
                "wholesale": float(p.wholesale_price or p.cost_price)
                if p
                else float(meta.get("wholesale") or 0),
                "selling": float(p.selling_price) if p else float(meta.get("selling") or 0),
            }
        )
    return out


def _top_products_from_api_items(
    items: list,
    products_by_id: dict,
    limit: int = 100,
) -> list[dict]:
    out = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or row.get("id") or "")
        qty = _dec(row.get("quantity") or row.get("qty") or row.get("sold_qty"))
        rev = _dec(
            row.get("revenue")
            or row.get("total")
            or row.get("amount")
            or row.get("sum")
        )
        p = products_by_id.get(pid) if pid else None
        if rev <= 0 and qty > 0:
            unit = _dec(row.get("unit_price") or row.get("price"))
            if unit <= 0 and p:
                unit = p.selling_price
            rev = qty * unit if unit > 0 else Decimal("0")
        if rev <= 0 and p and qty > 0:
            rev = qty * p.selling_price
        if rev <= 0 and qty <= 0:
            continue
        name = (
            (row.get("product_name") or row.get("name") or "")
            or (p.name if p else "")
            or (f"Mahsulot {pid[:8]}" if pid else "Mahsulot")
        )
        out.append(
            {
                "name": name,
                "image": (p.display_image if p else "") or str(row.get("image") or ""),
                "qty": float(qty),
                "revenue": float(rev),
                "stock": float(p.stock_qty) if p else float(_dec(row.get("stock"))),
                "wholesale": float(p.wholesale_price or p.cost_price)
                if p
                else float(_dec(row.get("wholesale_price"))),
                "selling": float(p.selling_price)
                if p
                else float(_dec(row.get("selling_price") or row.get("unit_price"))),
            }
        )
        if len(out) >= limit:
            break
    out.sort(key=lambda r: r["revenue"], reverse=True)
    return out[:limit]


@login_required
@require_GET
def cabinet_top_stats(request):
    """Top tovarlar — tanlangan sana oralig‘i bo‘yicha (AJAX)."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    today = timezone.localdate()

    custom_from = _parse_iso_date(request.GET.get("from"))
    custom_to = _parse_iso_date(request.GET.get("to"))
    if custom_from and custom_to:
        start, end = custom_from, custom_to
        if end < start:
            start, end = end, start
        if (end - start).days > 730:
            start = end - timedelta(days=730)
    else:
        start, end = today - timedelta(days=6), today

    span = (end - start).days + 1
    try:
        limit = max(1, min(100, int(request.GET.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100

    if span <= 1:
        detail_cap, max_pages, product_pages = 100, 4, 6
    elif span <= 7:
        detail_cap, max_pages, product_pages = 80, 4, 6
    elif span <= 31:
        detail_cap, max_pages, product_pages = 60, 4, 5
    else:
        detail_cap, max_pages, product_pages = 40, 3, 4

    memo_prefix = f"{server}|{(token or '')[-12:]}"

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_api = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|topapi|{start}|{end}|{limit}",
                    40.0,
                    lambda: tezpos_api.get_top_products(
                        token,
                        server,
                        days=span,
                        limit=limit,
                        date_from=start.isoformat(),
                        date_to=end.isoformat(),
                    ),
                )
            )
            fut_s = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|topsales|{start}|{end}|{max_pages}",
                    45.0,
                    lambda: tezpos_api.get_sales(
                        token,
                        server,
                        date_from=start.isoformat(),
                        date_to=end.isoformat(),
                        timeout=24,
                        max_pages=max_pages,
                    ),
                )
            )
            fut_p = pool.submit(
                lambda: _memo_get(
                    f"{memo_prefix}|products|{product_pages}",
                    90.0,
                    lambda: tezpos_api.get_products(
                        token, server, max_pages=product_pages
                    ),
                )
            )
            top_payload = fut_api.result() or {"items": []}
            sales = fut_s.result() or []
            products_raw = fut_p.result() or []
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    products_by_id = {str(p.id): p for p in products}
    products_by_name = _products_by_name(products)

    sales = [s for s in sales if isinstance(s, dict)]
    sales = [
        s
        for s in sales
        if (d := _sale_day(s)) is not None and start <= d <= end
    ]
    sales.sort(
        key=lambda s: _parse_dt(s.get("completed_at") or s.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    details = _fetch_sale_details(
        token,
        server,
        [str(s.get("id")) for s in sales if s.get("id")],
        limit=min(detail_cap, max(len(sales), 1)),
    )
    top_products = _top_products_from_details(
        details, products_by_id, products_by_name, limit=limit
    )
    if not top_products:
        top_products = _top_products_from_api_items(
            top_payload.get("items") or [], products_by_id, limit=limit
        )

    return JsonResponse(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "topProducts": top_products,
            "count": len(top_products),
        }
    )


@login_required
@require_GET
def cabinet_shift_detail(request):
    """Bitta smena: sotuv/foyda + narxlar ro'yxati (AJAX)."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    shift_id = (request.GET.get("id") or "").strip()
    opened = (request.GET.get("opened_at") or "").strip()
    closed = (request.GET.get("closed_at") or "").strip()
    sale_ids = [x for x in (request.GET.get("sale_ids") or "").split(",") if x.strip()]
    is_open = not closed or (request.GET.get("status") or "").strip() == "open"

    today = timezone.localdate()
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_pl = pool.submit(tezpos_api.get_price_lists, token, server)
            fut_p = pool.submit(
                lambda: tezpos_api.get_products(token, server, max_pages=3)
            )
            api_shift = {}
            if shift_id and not shift_id.startswith("session-"):
                fut_sh = pool.submit(tezpos_api.get_shift, token, server, shift_id)
            else:
                fut_sh = None
            price_lists = fut_pl.result() or []
            products_raw = fut_p.result() or []
            if fut_sh is not None:
                try:
                    api_shift = fut_sh.result() or {}
                except tezpos_api.TezPosApiError:
                    api_shift = {}
    except tezpos_api.TezPosApiError as exc:
        if getattr(exc, "status", None) in (401, 403):
            clear_tezpos_session(request)
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except (TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=504)

    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    products_by_id = {str(p.id): p for p in products}
    products_by_name = _products_by_name(products)
    price_lists = [pl for pl in price_lists if isinstance(pl, dict) and pl.get("is_active", True)]
    margin_ratio = _catalog_margin_ratio(products)

    opened_dt = _parse_dt(opened) or _parse_dt(api_shift.get("opened_at"))
    closed_dt = _parse_dt(closed) or _parse_dt(api_shift.get("closed_at"))
    if is_open or not closed_dt:
        closed_dt = timezone.now()
        is_open = True
    if opened_dt:
        date_from = timezone.localtime(opened_dt).date().isoformat()
        date_to = timezone.localtime(closed_dt).date().isoformat()
    else:
        date_from = (today - timedelta(days=2)).isoformat()
        date_to = today.isoformat()

    # Ochiq smena: ochilgandan hozirgacha — ko‘proq sahifa
    sale_pages = 6 if is_open else 3
    try:
        sales = tezpos_api.get_sales(
            token,
            server,
            date_from=date_from,
            date_to=date_to,
            timeout=28,
            max_pages=sale_pages,
        )
    except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    sales = [s for s in sales if isinstance(s, dict)]
    # Ochiq smena: vaqt oralig‘i (sale_ids eski/qisqa bo‘lishi mumkin)
    if is_open and opened_dt:
        matched = []
        for s in sales:
            dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
            if dt and opened_dt <= dt <= closed_dt:
                matched.append(s)
    elif sale_ids:
        idset = set(sale_ids)
        matched = [s for s in sales if str(s.get("id")) in idset]
        # Agar ID kesilgan bo‘lsa — ochilish vaqtidan yopilishgacha qo‘shimcha
        if opened_dt and len(matched) < len(sale_ids):
            seen = {str(s.get("id")) for s in matched}
            for s in sales:
                sid = str(s.get("id") or "")
                if sid in seen:
                    continue
                dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
                if dt and opened_dt <= dt <= closed_dt:
                    matched.append(s)
                    seen.add(sid)
    elif opened_dt:
        matched = []
        for s in sales:
            dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
            if dt and opened_dt <= dt <= closed_dt:
                matched.append(s)
    else:
        matched = sales

    gross = float(sum((_dec(s.get("total")) for s in matched), Decimal("0")))
    checks = len(matched)
    # Bir kunlik / ochiq smena — tezkor namuna (WAN)
    detail_cap = 16 if is_open or (opened_dt and date_from == date_to) else 12
    details = _fetch_sale_details(
        token,
        server,
        [str(s.get("id")) for s in matched if s.get("id")],
        limit=detail_cap,
        per_sale_timeout=3.5,
        overall_timeout=8.0,
    )
    lists = _aggregate_price_list_stats(
        list(details.values()), products_by_id, products_by_name, price_lists
    )
    # Namunani to‘liq smena tushumiga tenglashtirish (Sotuv+Optom = Savdo)
    lists = _scale_price_list_stats(lists, gross, checks)
    jami = next((r for r in lists if r.get("is_total")), None)
    if jami and jami.get("revenue", 0) > 0 and float(jami.get("revenue") or 0) > 0:
        # Scale qilingan Jami dan marja
        rev = float(jami["revenue"])
        ratio = float(jami.get("profit") or 0) / rev if rev else 0.0
        profit = gross * ratio if ratio else float(jami.get("profit") or 0)
        margin = ratio * 100.0
    else:
        profit = gross * float(margin_ratio)
        margin = (profit / gross * 100.0) if gross > 0 else 0.0

    return JsonResponse(
        {
            "id": shift_id,
            "summary": {
                "checks": checks,
                "gross": gross,
                "profit": profit,
                "margin": margin,
                "cost": gross - profit,
                "details_used": len(details),
            },
            "priceLists": [r for r in lists if not r.get("is_total")],
            "total": jami,
        }
    )

# --- telegram bot helpers & endpoints (appended) ---


def _shift_report_bundle(
    token: str,
    server: str,
    shift: dict,
) -> dict:
    """Smena uchun xabar + Excel ma'lumotlarini yig'adi."""
    from . import telegram_bot as tg

    today = timezone.localdate()
    products_raw = tezpos_api.get_products(token, server, max_pages=4) or []
    price_lists = tezpos_api.get_price_lists(token, server) or []
    products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
    products_by_id = {str(p.id): p for p in products}
    products_by_name = _products_by_name(products)
    price_lists = [pl for pl in price_lists if isinstance(pl, dict) and pl.get("is_active", True)]
    margin_ratio = _catalog_margin_ratio(products)

    opened_dt = _parse_dt(shift.get("opened_at"))
    closed_dt = _parse_dt(shift.get("closed_at"))
    is_open = (shift.get("status") or "") == "open" or not closed_dt
    if is_open or not closed_dt:
        closed_dt = timezone.now()
        is_open = True
    if opened_dt:
        date_from = timezone.localtime(opened_dt).date().isoformat()
        date_to = timezone.localtime(closed_dt).date().isoformat()
    else:
        date_from = (today - timedelta(days=1)).isoformat()
        date_to = today.isoformat()

    sales = tezpos_api.get_sales(
        token,
        server,
        date_from=date_from,
        date_to=date_to,
        timeout=28,
        max_pages=6 if is_open else 4,
    )
    sales = [s for s in sales if isinstance(s, dict)]
    sale_ids = set(str(x) for x in (shift.get("sale_ids") or []) if x)
    matched = []
    if opened_dt:
        for s in sales:
            dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
            if not dt:
                continue
            if opened_dt <= dt <= closed_dt:
                matched.append(s)
            elif sale_ids and str(s.get("id")) in sale_ids:
                matched.append(s)
    elif sale_ids:
        matched = [s for s in sales if str(s.get("id")) in sale_ids]
    else:
        matched = sales

    gross = float(sum((_dec(s.get("total")) for s in matched), Decimal("0")))
    checks = len(matched)
    details = _fetch_sale_details(
        token,
        server,
        [str(s.get("id")) for s in matched if s.get("id")],
        limit=100 if is_open else 70,
    )
    lists = _aggregate_price_list_stats(
        list(details.values()), products_by_id, products_by_name, price_lists
    )
    lists = _scale_price_list_stats(lists, gross, checks)
    jami = next((r for r in lists if r.get("is_total")), None)
    if jami and float(jami.get("revenue") or 0) > 0:
        rev = float(jami["revenue"])
        ratio = float(jami.get("profit") or 0) / rev if rev else 0.0
        profit = gross * ratio if ratio else float(jami.get("profit") or 0)
        margin = ratio * 100.0
    else:
        profit = gross * float(margin_ratio)
        margin = (profit / gross * 100.0) if gross > 0 else 0.0

    selling_rev = 0.0
    wholesale_rev = 0.0
    pl_rows = []
    for r in lists:
        if r.get("is_total"):
            continue
        rid = str(r.get("id") or "")
        rev = float(r.get("revenue") or 0)
        if rid == SELLING_LIST_ID:
            selling_rev += rev
        else:
            wholesale_rev += rev
        pl_rows.append(
            {
                "name": r.get("name") or "",
                "checks": r.get("checks") or 0,
                "revenue": rev,
                "profit": float(r.get("profit") or 0),
                "share": float(r.get("share") or 0),
            }
        )

    credit_agg: dict[str, dict] = defaultdict(lambda: {"orders": 0, "total": 0.0})
    sales_rows = []
    for s in matched:
        method = (s.get("payment_method") or s.get("payment") or "").strip().lower()
        total = float(_dec(s.get("total")))
        cust = (s.get("customer_name") or "").strip() or "—"
        dt = _parse_dt(s.get("completed_at") or s.get("created_at"))
        time_s = timezone.localtime(dt).strftime("%d.%m %H:%M") if dt else ""
        pay_label = PAYMENT_LABELS.get(method, method or "—")
        sid = str(s.get("id") or "")
        detail = details.get(sid) or {}
        if detail:
            _, pft = _estimate_sale_profit(
                detail, products_by_id, _dec(total), products_by_name, margin_ratio
            )
            sale_profit = float(pft)
        else:
            sale_profit = total * float(margin_ratio)
        sales_rows.append(
            {
                "time": time_s,
                "customer": cust,
                "payment": pay_label,
                "total": total,
                "profit": sale_profit,
            }
        )
        if method in ("credit", "qarz", "debt", "nasiya"):
            credit_agg[cust]["orders"] += 1
            credit_agg[cust]["total"] += total

    credit_rows = [
        {"customer": n, "orders": v["orders"], "total": v["total"]}
        for n, v in sorted(credit_agg.items(), key=lambda x: x[1]["total"], reverse=True)
    ]
    credit_total = sum(v["total"] for v in credit_rows)

    # To'liq qarzdorlar ro'yxati (API)
    debtors_rows: list[dict] = []
    debtors_total = Decimal("0")
    try:
        customers = tezpos_api.get_customers(token, server) or []
        for c in customers:
            if not isinstance(c, dict):
                continue
            debt = _debt_amount(c.get("debt"))
            if debt <= 0:
                continue
            debtors_total += debt
            debtors_rows.append(
                {
                    "name": (c.get("name") or "").strip() or "Mijoz",
                    "phone": (c.get("phone") or "").strip(),
                    "debt": float(debt),
                }
            )
        debtors_rows.sort(key=lambda r: r["debt"], reverse=True)
    except Exception:
        debtors_rows = []
        debtors_total = Decimal("0")

    # Kam qoldiq
    low_stock_rows = _build_near_min_stock(products)

    enriched = {
        **shift,
        "checks": checks,
        "gross": gross,
        "profit": profit,
        "margin": margin,
        "selling_revenue": selling_rev,
        "wholesale_revenue": wholesale_rev,
        "credit_total": credit_total,
        "credit_customers": credit_rows[:20],
        "debtors": debtors_rows,
        "debtors_count": len(debtors_rows),
        "debtors_total": float(debtors_total),
        "low_stock": low_stock_rows,
        "low_stock_count": len(low_stock_rows),
        "opened_at_display": shift.get("opened_display") or shift.get("opened_at") or "",
        "closed_at_display": shift.get("closed_display") or shift.get("closed_at") or "",
    }
    return {
        "shift": enriched,
        "sales_rows": sales_rows,
        "price_lists": pl_rows,
        "credit_rows": credit_rows,
        "debtors_rows": debtors_rows,
        "low_stock_rows": low_stock_rows,
        "excel": tg.build_shift_excel(
            business_name=shift.get("business_name") or "TezPOS",
            shift=enriched,
            sales_rows=sales_rows,
            price_lists=pl_rows,
            credit_rows=credit_rows,
            debtors_rows=debtors_rows,
            low_stock_rows=low_stock_rows,
        ),
    }


@login_required
@require_POST
def cabinet_bot_settings_save(request):
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    tenant = get_tenant_for_user(request.user)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    token = (body.get("token") or "").strip()
    recipients = (body.get("recipients") or "").strip()
    enabled = bool(body.get("enabled"))
    notify_open = bool(body.get("notify_open", True))
    notify_close = bool(body.get("notify_close", True))
    test_send = bool(body.get("test"))

    if enabled and not token:
        return JsonResponse({"error": "Bot token majburiy"}, status=400)
    if enabled and not recipients:
        return JsonResponse({"error": "Kamida bitta qabul qiluvchi kiriting"}, status=400)

    from . import telegram_bot as tg

    parsed = tg.parse_recipients(recipients)
    if enabled and not parsed:
        return JsonResponse(
            {
                "error": "Qabul qiluvchilar noto‘g‘ri. Chat ID, @username yoki t.me/... kiriting.",
            },
            status=400,
        )

    bot_info = None
    if token:
        bot_info = tg.get_me(token)
        if not bot_info.get("ok"):
            return JsonResponse(
                {
                    "error": f"Token ishlamadi: {bot_info.get('description') or 'xato'}",
                },
                status=400,
            )

    tenant.telegram_bot_token = token
    tenant.telegram_recipients = recipients
    tenant.telegram_enabled = enabled
    tenant.telegram_notify_open = notify_open
    tenant.telegram_notify_close = notify_close
    # Birinchi yoqishda eski smenalarni yubormaslik
    if enabled and not (tenant.telegram_notified_events or {}):
        tenant.telegram_notified_events = {"__seeded__": timezone.now().isoformat()}
    tenant.save(
        update_fields=[
            "telegram_bot_token",
            "telegram_recipients",
            "telegram_enabled",
            "telegram_notify_open",
            "telegram_notify_close",
            "telegram_notified_events",
        ]
    )

    test_results = []
    if test_send and token and parsed:
        uname = ((bot_info or {}).get("result") or {}).get("username") or "bot"
        msg = (
            f"✅ <b>TezPOS bot ulandi</b>\n"
            f"Bot: @{uname}\n"
            f"Biznes: {tenant.business_name}\n"
            f"Smena ochilish/yopilish xabarlari shu yerga keladi."
        )
        test_results = tg.broadcast_message(token, parsed, msg)

    return JsonResponse(
        {
            "ok": True,
            "enabled": tenant.telegram_enabled,
            "bot": ((bot_info or {}).get("result") or {}),
            "recipients": parsed,
            "test": test_results,
        }
    )


@login_required
@require_GET
def cabinet_bot_chats(request):
    """Botga /start yozgan chatlarni ko‘rsatadi (Chat ID olish)."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    tenant = get_tenant_for_user(request.user)
    token = (request.GET.get("token") or tenant.telegram_bot_token or "").strip()
    if not token:
        return JsonResponse({"error": "Avval bot tokenini kiriting"}, status=400)
    from . import telegram_bot as tg

    bot_info = tg.get_me(token)
    if not bot_info.get("ok"):
        return JsonResponse(
            {"error": f"Token ishlamadi: {bot_info.get('description') or 'xato'}"},
            status=400,
        )
    chats = tg.list_recent_chats(token)
    return JsonResponse(
        {
            "ok": True,
            "bot": bot_info.get("result") or {},
            "chats": chats,
            "hint": (
                "Ro‘yxat bo‘sh bo‘lsa: Telegramda botga kirib /start yozing, "
                "keyin shu tugmani qayta bosing."
            ),
        }
    )


@login_required
@require_GET
def cabinet_telegram_sync(request):
    """Yangi ochilgan/yopilgan smenalarni aniqlab Telegramga yuboradi."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)

    tenant = get_tenant_for_user(request.user)
    if not tenant.telegram_enabled or not tenant.telegram_bot_token:
        return JsonResponse({"ok": True, "skipped": True, "reason": "disabled"})

    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    # Fon cron uchun credential yangilab turish
    if token and server:
        TenantProfile.objects.filter(pk=tenant.pk).update(
            tezpos_api_token=token,
            tezpos_server_name=server,
        )
        tenant.tezpos_api_token = token
        tenant.tezpos_server_name = server

    try:
        result = sync_telegram_shifts_for_tenant(tenant, token, server)
    except (tezpos_api.TezPosApiError, TimeoutError, OSError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse(result)


@require_GET
def telegram_cron_sync(request):
    """
    Brauzersiz smena sync — systemd/cron har 1 daqiqada.
    Header: X-Cron-Secret yoki ?secret=
    """
    from django.conf import settings as dj_settings

    expected = (getattr(dj_settings, "TELEGRAM_CRON_SECRET", "") or "").strip()
    got = (
        request.headers.get("X-Cron-Secret")
        or request.GET.get("secret")
        or ""
    ).strip()
    if not expected or got != expected:
        return JsonResponse({"error": "forbidden"}, status=403)

    tenants = TenantProfile.objects.filter(
        telegram_enabled=True,
    ).exclude(telegram_bot_token="").exclude(tezpos_api_token="").exclude(
        tezpos_server_name=""
    )
    results = []
    for tenant in tenants:
        try:
            results.append(
                {
                    "tenant": tenant.business_name,
                    **sync_telegram_shifts_for_tenant(
                        tenant,
                        tenant.tezpos_api_token,
                        tenant.tezpos_server_name,
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"tenant": tenant.business_name, "error": str(exc)})
    return JsonResponse({"ok": True, "results": results})


def sync_telegram_shifts_for_tenant(tenant, token: str, server: str) -> dict:
    """
    Smena ochilish/yopilishni tekshiradi.
    Yopilishda: avval TEZKOR xabar, keyin Excel (kechikishsiz bildirishnoma).
    """
    from . import telegram_bot as tg

    recipients = tg.parse_recipients(tenant.telegram_recipients)
    if not recipients:
        return {"ok": True, "skipped": True, "reason": "no_recipients"}

    today = timezone.localdate()

    try:
        raw_shifts = tezpos_api.get_shifts(token, server) or []
    except (tezpos_api.TezPosApiError, TimeoutError, OSError):
        raise

    shifts = []
    for raw in raw_shifts:
        if isinstance(raw, dict):
            shifts.append(_normalize_api_shift(raw, today))

    if not shifts:
        try:
            sales = tezpos_api.get_sales(
                token,
                server,
                date_from=(today - timedelta(days=2)).isoformat(),
                date_to=today.isoformat(),
                timeout=18,
                max_pages=3,
            )
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            sales = []
        products_raw = []
        try:
            products_raw = tezpos_api.get_products(token, server, max_pages=2) or []
        except (tezpos_api.TezPosApiError, TimeoutError, OSError):
            pass
        products = [_map_product(p) for p in products_raw if isinstance(p, dict)]
        margin_ratio = _catalog_margin_ratio(products)
        shifts = _build_shifts_from_sales(
            [s for s in sales if isinstance(s, dict)],
            today=today,
            gap_hours=4.0,
            margin_ratio=margin_ratio,
        )

    notified = dict(tenant.telegram_notified_events or {})
    sent: list[dict] = []
    # Birinchi sync: mavjud smenalarni faqat belgilash (spam bo‘lmasin)
    seed_only = "__seeded__" in notified and len(notified) <= 2
    if seed_only:
        for sh in shifts[:20]:
            sid = str(sh.get("id") or "").strip()
            if not sid:
                continue
            st = sh.get("status") or "closed"
            if st == "open":
                notified[f"{sid}:open"] = timezone.now().isoformat()
            else:
                notified[f"{sid}:close"] = timezone.now().isoformat()
                notified.setdefault(f"{sid}:open", timezone.now().isoformat())
                notified.setdefault(f"{sid}:excel", timezone.now().isoformat())
        notified.pop("__seeded__", None)
        tenant.telegram_notified_events = notified
        tenant.save(update_fields=["telegram_notified_events"])
        return {"ok": True, "sent": [], "checked": len(shifts), "seeded": True}

    for sh in shifts[:12]:
        sid = str(sh.get("id") or "").strip()
        if not sid:
            continue
        status = sh.get("status") or "closed"
        for event, enabled in (
            ("open", tenant.telegram_notify_open and status == "open"),
            ("close", tenant.telegram_notify_close and status == "closed"),
        ):
            if not enabled:
                continue
            key = f"{sid}:{event}"
            if key in notified:
                continue
            if event == "open":
                opened_dt = _parse_dt(sh.get("opened_at"))
                if opened_dt and timezone.now() - opened_dt > timedelta(hours=36):
                    notified[key] = timezone.now().isoformat()
                    continue
            if event == "close":
                closed_dt = _parse_dt(sh.get("closed_at")) or _parse_dt(sh.get("opened_at"))
                if closed_dt and timezone.now() - closed_dt > timedelta(days=3):
                    notified[key] = timezone.now().isoformat()
                    notified.setdefault(f"{sid}:excel", timezone.now().isoformat())
                    continue

            try:
                # Tezkor xabar — Excel kutmasdan (2 daqiqa ichida yetishi uchun)
                quick = dict(sh)
                quick["business_name"] = tenant.business_name
                quick["opened_at_display"] = sh.get("opened_display") or sh.get("opened_at")
                quick["closed_at_display"] = sh.get("closed_display") or sh.get("closed_at")
                text = tg.build_shift_message(
                    business_name=tenant.business_name,
                    event=event,
                    shift=quick,
                )
                msg_res = tg.broadcast_message(tenant.telegram_bot_token, recipients, text)
                notified[key] = timezone.now().isoformat()
                tenant.telegram_notified_events = notified
                tenant.save(update_fields=["telegram_notified_events"])

                doc_res = []
                if event == "close":
                    excel_key = f"{sid}:excel"
                    if excel_key not in notified:
                        try:
                            bundle = _shift_report_bundle(token, server, sh)
                            if bundle.get("excel"):
                                day = timezone.localdate().isoformat()
                                fname = f"kunlik_hisobot_{day}_{sid[:8]}.xlsx"
                                doc_res = tg.broadcast_document(
                                    tenant.telegram_bot_token,
                                    recipients,
                                    fname,
                                    bundle["excel"],
                                    caption=(
                                        f"📎 Kunlik hisobot — {tenant.business_name}\n"
                                        f"Sotuv · Qarzdorlar · Kam qoldiq · Excel"
                                    ),
                                )
                            notified[excel_key] = timezone.now().isoformat()
                            tenant.telegram_notified_events = notified
                            tenant.save(update_fields=["telegram_notified_events"])
                        except Exception as excel_exc:  # noqa: BLE001
                            sent.append(
                                {
                                    "shift_id": sid,
                                    "event": "excel",
                                    "error": str(excel_exc),
                                }
                            )

                sent.append(
                    {
                        "shift_id": sid,
                        "event": event,
                        "messages": msg_res,
                        "documents": doc_res,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                sent.append({"shift_id": sid, "event": event, "error": str(exc)})

    # Yopilgan smenalar uchun Excel hali ketmagan bo‘lsa — alohida urinish
    if tenant.telegram_notify_close:
        for sh in shifts[:12]:
            sid = str(sh.get("id") or "").strip()
            if not sid or (sh.get("status") or "") != "closed":
                continue
            close_key = f"{sid}:close"
            excel_key = f"{sid}:excel"
            if close_key not in notified or excel_key in notified:
                continue
            try:
                bundle = _shift_report_bundle(token, server, sh)
                if bundle.get("excel"):
                    day = timezone.localdate().isoformat()
                    fname = f"kunlik_hisobot_{day}_{sid[:8]}.xlsx"
                    doc_res = tg.broadcast_document(
                        tenant.telegram_bot_token,
                        recipients,
                        fname,
                        bundle["excel"],
                        caption=f"📎 Kunlik hisobot — {tenant.business_name}",
                    )
                    notified[excel_key] = timezone.now().isoformat()
                    sent.append(
                        {"shift_id": sid, "event": "excel", "documents": doc_res}
                    )
            except Exception as exc:  # noqa: BLE001
                sent.append({"shift_id": sid, "event": "excel", "error": str(exc)})

    if len(notified) > 120:
        items = sorted(notified.items(), key=lambda x: x[1])
        notified = dict(items[-80:])

    tenant.telegram_notified_events = notified
    tenant.save(update_fields=["telegram_notified_events"])

    return {"ok": True, "sent": sent, "checked": len(shifts)}


def _debt_amount(value) -> Decimal:
    try:
        return Decimal(str(value or 0).replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _site_check_url(request, check_path_or_url: str) -> str:
    """API yoki nisbiy chek yo‘lini shu saytdagi /check/... URL ga aylantiradi."""
    raw = (check_path_or_url or "").strip()
    if not raw:
        return ""
    m = re.search(r"/check/([^/]+)/([^/]+)/?", raw)
    if m:
        path = reverse(
            "public-receipt-check",
            kwargs={"server_name": m.group(1), "ref": m.group(2).rstrip("/")},
        )
        return request.build_absolute_uri(path)
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    return request.build_absolute_uri(raw)


def _sms_check_url(request, check_path_or_url: str) -> str:
    """SMS uchun ochiladigan chek — localhost o‘rniga tez-pos.uz yoki API."""
    url = _site_check_url(request, check_path_or_url)
    if url and "127.0.0.1" not in url and "localhost" not in url:
        return url
    m = re.search(r"/check/([^/]+)/([^/]+)/?", check_path_or_url or url or "")
    if not m:
        return url
    slug, ref = m.group(1), m.group(2).rstrip("/")
    # Production sayt
    return f"https://tez-pos.uz/check/{slug}/{ref}/"


def _money_sms(value) -> str:
    try:
        n = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        n = Decimal("0")
    return f"{n:,.0f}".replace(",", " ")


def _build_debt_payment_sms(
    *,
    store: str,
    cashier: str,
    paid,
    balance,
    check_url: str,
) -> str:
    """Qarz to‘lovi SMS matni (DevSMS)."""
    who = " - ".join(x for x in [(cashier or "").strip(), (store or "").strip()] if x) or "TezPOS"
    lines = [
        who,
        f"To‘lov: {_money_sms(paid)} so'm",
        f"Qoldiq: {_money_sms(balance)} so'm",
    ]
    if check_url:
        lines.append(f"Chek: {check_url}")
    return "\n".join(lines)


@login_required
@require_GET
def cabinet_debtors(request):
    """TezPOS API dan qarzdorlar ro‘yxati."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    try:
        customers = tezpos_api.get_customers(token, server)
    except tezpos_api.TezPosApiError as exc:
        if exc.status in (401, 403):
            return JsonResponse({"error": "auth"}, status=401)
        return JsonResponse({"error": str(exc)}, status=502)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=502)

    rows = []
    total = Decimal("0")
    for c in customers or []:
        if not isinstance(c, dict):
            continue
        debt = _debt_amount(c.get("debt"))
        if debt <= 0:
            continue
        total += debt
        name = (c.get("name") or "").strip() or "Mijoz"
        phone = (c.get("phone") or "").strip()
        rows.append(
            {
                "id": str(c.get("id") or ""),
                "name": name,
                "phone": phone,
                "debt": float(debt),
                "debt_display": f"{debt:,.0f}".replace(",", " "),
            }
        )
    rows.sort(key=lambda r: r["debt"], reverse=True)
    return JsonResponse(
        {
            "ok": True,
            "count": len(rows),
            "total_debt": float(total),
            "total_debt_display": f"{total:,.0f}".replace(",", " "),
            "debtors": rows,
        }
    )


@login_required
@require_POST
def cabinet_debtor_pay(request):
    """Qarzning bir qismi yoki to‘liq to‘lovi — TezPOS pay-debt API."""
    if not session_has_tezpos(request):
        return JsonResponse({"error": "auth"}, status=401)
    token = request.session[SESSION_TOKEN]
    server = request.session[SESSION_SERVER]
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    customer_id = str(body.get("customer_id") or "").strip()
    if not customer_id:
        return JsonResponse({"error": "Mijoz tanlanmagan."}, status=400)

    amount = _debt_amount(body.get("amount"))
    if amount <= 0:
        return JsonResponse({"error": "To‘lov summasi 0 dan katta bo‘lishi kerak."}, status=400)

    payment_type = str(body.get("payment_type") or "cash").strip().lower()
    if payment_type not in ("cash", "card"):
        payment_type = "cash"
    note = str(body.get("note") or "").strip()[:255]

    try:
        site_base = request.build_absolute_uri("/").rstrip("/")
        # Localhost DevSMS/chek uchun yaroqsiz
        if "127.0.0.1" in site_base or "localhost" in site_base:
            check_base = "https://tez-pos.uz"
        else:
            check_base = site_base
        result = tezpos_api.pay_customer_debt(
            token,
            server,
            customer_id,
            amount,
            payment_type=payment_type,
            note=note,
            check_base_url=check_base,
        )
    except tezpos_api.TezPosApiError as exc:
        if exc.status in (401, 403):
            return JsonResponse({"error": "auth"}, status=401)
        msg = str(exc)
        if isinstance(exc.payload, (bytes, bytearray)):
            try:
                payload = json.loads(exc.payload.decode("utf-8", errors="replace"))
                if isinstance(payload, dict) and payload.get("detail"):
                    msg = str(payload["detail"])
            except Exception:
                pass
        return JsonResponse({"error": msg}, status=exc.status or 502)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=502)

    if not isinstance(result, dict):
        return JsonResponse({"error": "Server javobi noto‘g‘ri"}, status=502)

    check_raw = (
        result.get("check_path")
        or result.get("check_url")
        or result.get("receipt_url")
        or ""
    )
    check_url = _site_check_url(request, str(check_raw))
    sms_check_url = _sms_check_url(request, str(check_raw) or check_url)

    customer = result.get("customer") if isinstance(result.get("customer"), dict) else {}
    balance = _debt_amount(result.get("balance", customer.get("debt")))
    paid = _debt_amount(result.get("paid", amount))

    phone = (
        str(customer.get("phone") or "")
        or str(result.get("phone") or "")
        or str(body.get("phone") or "")
    ).strip()
    if not phone:
        try:
            for c in tezpos_api.get_customers(token, server) or []:
                if isinstance(c, dict) and str(c.get("id") or "") == customer_id:
                    phone = str(c.get("phone") or "").strip()
                    if not customer.get("name") and c.get("name"):
                        customer["name"] = c.get("name")
                    break
        except Exception:
            pass

    sms_sent = bool(result.get("sms_sent"))
    if not sms_sent and isinstance(result.get("sms"), dict):
        sms_sent = bool(result["sms"].get("ok") or result["sms"].get("sent"))
    elif not sms_sent and result.get("sms") is True:
        sms_sent = True

    sms_error = ""
    # TezPOS DevSMS — to'g'ridan-to'g'ri (backend SMS ishonchsiz bo'lishi mumkin)
    from . import devsms

    store = str(
        result.get("store_name")
        or request.session.get(SESSION_DISPLAY)
        or server
        or "TezPOS"
    )
    cashier = str(result.get("cashier") or result.get("cashier_name") or "admin")
    # TezPOS: to'lovda Qarz manfiy bo'ladi
    sms_text = devsms.build_debt_message(
        shop=cashier,
        branch=store,
        debt_amount=-abs(paid),
        balance=balance,
        check_link=sms_check_url,
    )
    sms_res = devsms.send_dev_sms(phone=phone, message=sms_text)
    sms_sent = bool(sms_res.get("ok"))
    if not sms_sent:
        sms_error = str(sms_res.get("error") or "SMS yuborilmadi (DevSMS).")

    return JsonResponse(
        {
            "ok": True,
            "paid": float(paid),
            "paid_display": f"{paid:,.0f}".replace(",", " "),
            "balance": float(balance),
            "balance_display": f"{balance:,.0f}".replace(",", " "),
            "receipt_number": result.get("receipt_number"),
            "payment_type": result.get("payment_type") or payment_type,
            "check_url": check_url,
            "sms_sent": sms_sent,
            "sms_error": sms_error,
            "sms_phone": phone,
            "sms_preview": sms_text,
            "customer": {
                "id": str(customer.get("id") or customer_id),
                "name": customer.get("name") or "",
                "phone": phone,
                "debt": float(balance),
            },
        }
    )
