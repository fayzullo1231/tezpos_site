"""
Ommaviy elektron chek — to'liq HTML/dizayn shu saytda (tezpos_site).

URL: /check/<server_name>/<ref>/
Ma'lumot: TezPOS backend (/check/... yoki /api/public/check/...)
Logo: /static/logo.png
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils.html import escape
from django.views import View

from accounts.tezpos_api import TezPosApiError, api_request, normalize_api_base


def _logo_url(request) -> str:
    try:
        return request.build_absolute_uri(static("logo.png"))
    except Exception:
        return static("logo.png")


def _payload_dict(exc: TezPosApiError) -> dict | None:
    raw = getattr(exc, "payload", None)
    if not raw:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _fmt_money_label(raw: str) -> str:
    """'-9 000 so'm' → '9 000 so'm'."""
    s = (raw or "").strip()
    if s.startswith("-"):
        s = s[1:].lstrip()
    return s


def _normalize_check_ctx(data: dict) -> dict:
    """To'lov cheklarida 'Qarz: -X' o'rniga musbat 'To'lov' summasi."""
    kind = data.get("kind") or "not_found"
    debt_amount = str(data.get("debt_amount") or "")
    is_payment = kind == "payment"
    if is_payment:
        debt_amount = _fmt_money_label(debt_amount)
    return {
        "title": data.get("title") or "Chek",
        "store_name": data.get("store_name") or "TezPOS",
        "subtitle": data.get("subtitle") or "Elektron chek",
        "kind": kind,
        "meta_rows": data.get("meta_rows") or [],
        "items": data.get("items") or [],
        "total": data.get("total") or "",
        "paid": data.get("paid") or "",
        "show_debt": bool(data.get("show_debt")),
        "debt_amount": debt_amount,
        "debt_balance": data.get("debt_balance") or "",
        "empty_title": data.get("empty_title") or "Chek topilmadi",
        "empty_detail": data.get("empty_detail") or "",
        "is_payment": is_payment,
    }


def _fetch_backend_check_html(slug: str, ref: str) -> bytes | None:
    url = f"{normalize_api_base()}/check/{slug}/{ref}/"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "TezPOS-Site-Cabinet/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if raw and b"<html" in raw[:800].lower():
                return raw
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    return None


def _rewrite_payment_labels_in_html(html: bytes) -> bytes:
    """
    To'lov cheklarida backend 'Qarz' + manfiy summa yuborsa —
    'To'lov' + musbat summaga almashtiramiz (SMS matnidagi xato bilan bir xil).
    """
    try:
        text = html.decode("utf-8")
    except Exception:
        return html

    # >Qarz</span>...<strong>-9 000 so'm</strong>  →  To'lov + musbat
    text = re.sub(
        r"(>)(Qarz)(</span>\s*<strong[^>]*>)\s*-+\s*",
        r"\1To'lov\3",
        text,
        count=5,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


class PublicReceiptCheckView(View):
    template_name = "sales/public_check.html"

    def get(self, request, server_name: str, ref: str):
        slug = (server_name or "").strip()
        ref = (ref or "").strip().rstrip("/")
        logo = _logo_url(request)

        data = None
        try:
            data = api_request(
                "GET",
                f"/api/public/check/{slug}/{ref}/",
            )
        except TezPosApiError as exc:
            payload = _payload_dict(exc)
            if payload:
                data = payload
        except Exception:
            data = None

        if isinstance(data, dict) and (data.get("kind") or data.get("items") is not None):
            ctx = _normalize_check_ctx(data)
            ctx["logo_url"] = logo
            status = 404 if ctx["kind"] == "not_found" else 200
            return render(request, self.template_name, ctx, status=status)

        html = _fetch_backend_check_html(slug, ref)
        if html:
            return HttpResponse(
                _rewrite_payment_labels_in_html(html),
                content_type="text/html; charset=utf-8",
                status=200,
            )

        return self._empty(
            request,
            logo,
            title="Chek topilmadi",
            empty_title="Chek topilmadi",
            empty_detail=f"{escape(slug)} — <code>{escape(ref)}</code>",
            status=404,
        )

    def _empty(self, request, logo, *, title, empty_title, empty_detail, status=404):
        return render(
            request,
            self.template_name,
            {
                "title": title,
                "logo_url": logo,
                "store_name": "TezPOS",
                "subtitle": "Elektron chek",
                "kind": "not_found",
                "empty_title": empty_title,
                "empty_detail": empty_detail,
            },
            status=status,
        )
