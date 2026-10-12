"""
Sotuv paytidagi sotib olish narxi.

Katalogdagi cost_price — faqat hozirgi narx. Narx o‘zgargach eski sotuvlar
yangi narx bilan hisoblanib foyda noto‘g‘ri chiqadi (masalan 9300 da olinib
9500 da sotilgan tovar, keyin narx 9900 ga ko‘tarilsa — «zarar»).
Shuning uchun har bir sotuv qatoriga shu vaqtgacha bo‘lgan oxirgi kirim narxi
`unit_cost` sifatida yoziladi.
"""
from __future__ import annotations

import bisect
import logging
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

TIMELINE_TTL = 30 * 60
LOOKBACK_DAYS = 400

_timelines: dict[str, tuple[float, dict[str, tuple[list[float], list[Decimal]]]]] = {}
_loading: set[str] = set()
_lock = threading.Lock()


def _ts(raw) -> float | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        dt = parse_datetime(str(raw).replace("Z", "+00:00"))
        if dt is None:
            return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.timestamp()


def _money(raw) -> Decimal:
    try:
        return Decimal(str(raw or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _build_timeline(receipts: list) -> dict[str, tuple[list[float], list[Decimal]]]:
    points: dict[str, list[tuple[float, Decimal]]] = {}
    for r in receipts:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "completed").lower() not in ("completed", "done", "posted"):
            continue
        at = _ts(r.get("completed_at") or r.get("created_at") or r.get("date"))
        if at is None:
            continue
        for it in r.get("items") or []:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("product") or it.get("product_id") or "").strip()
            cost = _money(it.get("cost_price") or it.get("unit_cost") or it.get("price"))
            if pid and cost > 0:
                points.setdefault(pid, []).append((at, cost))
    out = {}
    for pid, rows in points.items():
        rows.sort(key=lambda x: x[0])
        out[pid] = ([t for t, _ in rows], [c for _, c in rows])
    return out


def _load(token: str, server: str) -> None:
    from . import tezpos_api

    key = server.lower()
    try:
        today = timezone.localdate()
        receipts = tezpos_api.get_stock_receipts(
            token,
            server,
            date_from=(today - timedelta(days=LOOKBACK_DAYS)).isoformat(),
            date_to=today.isoformat(),
            timeout=30,
            max_pages=400,
        ) or []
        timeline = _build_timeline(receipts)
        with _lock:
            _timelines[key] = (time.time(), timeline)
        logger.info("cost timeline server=%s products=%s receipts=%s", key, len(timeline), len(receipts))
    except Exception:
        logger.exception("cost timeline load failed server=%s", key)
    finally:
        with _lock:
            _loading.discard(key)


def get_timeline(token: str, server: str):
    """Keshdagi tarix; eskirgan/yo‘q bo‘lsa fonda yangilaydi (so‘rovni kutdirmaydi)."""
    if not token or not server:
        return None
    key = server.lower()
    with _lock:
        hit = _timelines.get(key)
        stale = not hit or time.time() - hit[0] > TIMELINE_TTL
        if stale and key not in _loading:
            _loading.add(key)
            threading.Thread(
                target=_load, args=(token, server), name=f"cost-tl-{key[:20]}", daemon=True
            ).start()
    return hit[1] if hit else None


def cost_at(timeline, product_id: str, at: float | None) -> Decimal | None:
    """Sotuv vaqtigacha bo‘lgan oxirgi kirim narxi; oxirgi kirimdan keyin — None (katalog narxi)."""
    if not timeline or not product_id or at is None:
        return None
    row = timeline.get(str(product_id))
    if not row:
        return None
    times, costs = row
    i = bisect.bisect_right(times, at)
    if i >= len(times):
        return None
    if i == 0:
        return costs[0]
    return costs[i - 1]


def annotate_sales(sales, token: str, server: str):
    """Sotuv qatorlariga sotuv paytidagi tannarxni (unit_cost) yozadi."""
    if not sales:
        return sales
    timeline = get_timeline(token, server)
    if not timeline:
        return sales
    rows = sales if isinstance(sales, list) else [sales]
    for sale in rows:
        if not isinstance(sale, dict):
            continue
        at = _ts(sale.get("completed_at") or sale.get("created_at") or sale.get("sold_at"))
        for it in sale.get("items") or []:
            if not isinstance(it, dict):
                continue
            if it.get("batch_allocations") or it.get("batches"):
                continue
            if any(it.get(k) for k in ("unit_cost", "cost_price", "purchase_price", "buy_price")):
                continue
            pid = it.get("product_id") or it.get("product")
            if isinstance(pid, dict):
                pid = pid.get("id")
            cost = cost_at(timeline, str(pid or ""), at)
            if cost is not None:
                it["unit_cost"] = str(cost)
    return sales
