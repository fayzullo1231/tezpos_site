"""
DevSMS (https://devsms.uz) — TezPOS dagi qarz SMS shabloni bilan.

Manba: TezPOS/app/devSms.cjs + MpBuildDebtMessage
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings


DEVSMS_URL = "https://devsms.uz/api/send_sms.php"


def normalize_phone(raw: str) -> str:
    """TezPOS MpNormalizePhone."""
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not d:
        return ""
    if d.startswith("998") and len(d) == 12:
        return d
    if len(d) == 9:
        return "998" + d
    if d.startswith("0") and len(d) == 10:
        return "998" + d[1:]
    return d if len(d) >= 9 else ""


def _fmt_som(value) -> str:
    try:
        n = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        n = Decimal("0")
    # TezPOS: manfiy summalar uchun minus saqlanadi
    sign = "-" if n < 0 else ""
    abs_n = abs(n)
    return f"{sign}{abs_n:,.0f}".replace(",", " ")


def build_debt_message(
    *,
    shop: str,
    branch: str = "",
    debt_amount,
    balance,
    check_link: str = "",
) -> str:
    """
    TezPOS MpBuildDebtMessage:
      {shop} - {branch}
      Qarz: X so'm
      Qoldiq: Y so'm
      Chek: url
    To'lovda debt_amount manfiy bo'ladi (masalan -9000).
    """
    shop = (shop or "").strip() or "TezPOS"
    branch = (branch or "").strip()
    head = f"{shop} - {branch}" if branch else shop
    lines = [
        head,
        f"Qarz: {_fmt_som(debt_amount)} so'm",
        f"Qoldiq: {_fmt_som(balance)} so'm",
        f"Chek: {(check_link or '').strip() or '—'}",
    ]
    return "\n".join(lines)


def resolve_token(explicit: str | None = None) -> str:
    """Env → settings → TezPOS lokal token fayllari."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env = (os.environ.get("DEVSMS_TOKEN") or "").strip()
    if env:
        return env
    conf = str(getattr(settings, "DEVSMS_TOKEN", "") or "").strip()
    if conf:
        return conf

    candidates = [
        Path(settings.BASE_DIR) / "devsms-token.txt",
        Path(r"C:\Users\User\Documents\TezPOS\app\devsms-token.txt"),
        Path(r"C:\Users\User\Documents\TezPOS\app\release\win-unpacked\resources\devsms-token.txt"),
    ]
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        candidates.extend(
            [
                Path(appdata) / "tezpos" / "devsms-token.txt",
                Path(appdata) / "TezPOS" / "devsms-token.txt",
            ]
        )
    for p in candidates:
        try:
            if p.is_file():
                t = p.read_text(encoding="utf-8").lstrip("\ufeff").strip()
                if t:
                    return t
        except OSError:
            continue
    return ""


def send_dev_sms(
    *,
    phone: str,
    message: str,
    token: str | None = None,
    sender: str | None = None,
) -> dict:
    """
    TezPOS sendDevSms — to'g'ridan-to'g'ri DevSMS API.
    Qaytaradi: {ok, error?, phone?, sms_id?, status?}
    """
    auth = resolve_token(token)
    if not auth:
        return {"ok": False, "error": "DevSMS token yo‘q (DEVSMS_TOKEN yoki TezPOS token fayli)."}

    to = normalize_phone(phone)
    if not to:
        return {"ok": False, "error": "Mijoz telefon raqami yo‘q yoki noto‘g‘ri."}

    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "SMS matni bo‘sh."}

    from_id = (sender or getattr(settings, "DEVSMS_FROM", "") or "4546").strip() or "4546"
    body = {"phone": to, "message": text, "from": from_id}

    req = urllib.request.Request(
        DEVSMS_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TezPOS-Site/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            if not (isinstance(data, dict) and data.get("success")):
                err = ""
                if isinstance(data, dict):
                    err = str(data.get("error") or data.get("message") or "")
                return {"ok": False, "error": err or f"DevSMS xato ({resp.status})"}
            payload = data.get("data") if isinstance(data.get("data"), dict) else {}
            return {
                "ok": True,
                "phone": to,
                "sms_id": payload.get("sms_id"),
                "status": payload.get("status") or "sent",
                "cost": payload.get("total_cost"),
                "balance": payload.get("balance"),
            }
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        try:
            data = json.loads(err_body) if err_body else {}
            err = str(data.get("error") or data.get("message") or err_body or exc)
        except Exception:
            err = err_body or str(exc)
        return {"ok": False, "error": err}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
