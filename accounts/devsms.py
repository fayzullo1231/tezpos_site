"""
DevSMS (https://devsms.uz) — TezPOS dagi qarz SMS shabloni bilan.

Manba: TezPOS/app/devSms.cjs + MpBuildDebtMessage
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings


DEVSMS_URL = "https://devsms.uz/api/send_sms.php"
DEVSMS_TEMPLATE_URLS = (
    "https://devsms.uz/api/add_template.php",
    "https://devsms.uz/api/submit_template.php",
    "https://devsms.uz/api/template.php",
    "https://devsms.uz/api/templates.php",
)


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
    Tasdiqlangan Eskiz shablon:
      Kulol Optom-Oziq ovqat:
      Qarzdorligingiz : 1 456 000 so‘m.
      Iltimos, qarzdorlikni to‘lashni unutmang.
    """
    shop = (shop or "").strip() or "TezPOS"
    branch = (branch or "").strip()
    if branch and f"-{branch}" not in shop:
        head = f"{shop}-{branch}"
    else:
        head = shop
    try:
        bal = Decimal(str(balance if balance is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        bal = Decimal("0")
    try:
        delta = Decimal(str(debt_amount if debt_amount is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        delta = Decimal("0")
    show = abs(bal) if bal != 0 else abs(delta)
    # Apostrof: so‘m / to‘lashni — Eskiz shablonidagi belgi (U+2018)
    return (
        f"{head}:\n"
        f"Qarzdorligingiz : {_fmt_som(show)} so‘m.\n"
        f"Iltimos, qarzdorlikni to‘lashni unutmang."
    )


def sample_debt_template(shop: str) -> str:
    """Moderatsiyaga yuboriladigan namuna."""
    label = (shop or "").strip() or "Kulol Optom-Oziq ovqat"
    return build_debt_message(
        shop=label,
        debt_amount=1456000,
        balance=1456000,
    )


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


def _api_post(url: str, auth: str, payload: dict, *, form: bool = False) -> dict:
    if form:
        data = urllib.parse.urlencode(
            {k: v for k, v in payload.items() if v is not None}
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "TezPOS-Site/1.0",
        }
    else:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TezPOS-Site/1.0",
        }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data_out = json.loads(raw) if raw else {}
            return {"http": resp.status, "data": data_out if isinstance(data_out, dict) else {"raw": raw}}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        try:
            data_out = json.loads(err_body) if err_body else {}
        except Exception:
            data_out = {"error": err_body or str(exc)}
        return {"http": exc.code, "data": data_out if isinstance(data_out, dict) else {"error": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        return {"http": 0, "data": {"error": str(exc)}}


def _is_moderation_error(err: str) -> bool:
    low = (err or "").lower()
    keys = (
        "модерац",
        "moderats",
        "template",
        "shablon",
        "шаблон",
        "мои тексты",
        "eskiz",
        "ещё не прошёл",
        "еще не прошел",
        "прошёл модерацию",
        "прошел модерацию",
    )
    return any(k in low for k in keys)


def submit_template(message: str, *, token: str | None = None) -> dict:
    """
    SMS matnini DevSMS/Eskiz moderatsiyasiga yuboradi.
    Bir nechta endpoint/format urinadi (DevSMS UI: Шаблоны → Отправить шаблон).
    """
    auth = resolve_token(token)
    if not auth:
        return {"ok": False, "error": "DevSMS token yo‘q."}
    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "Shablon matni bo‘sh."}

    payloads = (
        {"template": text},
        {"message": text},
        {"text": text},
        {"sms_text": text},
        {"content": text},
    )
    attempts = []
    for url in DEVSMS_TEMPLATE_URLS:
        for payload in payloads:
            for as_form in (False, True):
                res = _api_post(url, auth, payload, form=as_form)
                attempts.append({"url": url, "form": as_form, "http": res.get("http"), "data": res.get("data")})
                data = res.get("data") or {}
                if res.get("http") in (200, 201) and (
                    data.get("success") is True
                    or data.get("ok") is True
                    or data.get("status") in ("ok", "success", "pending", "moderation", "waiting")
                    or data.get("template")
                    or data.get("id")
                ):
                    return {
                        "ok": True,
                        "message": str(
                            data.get("message")
                            or data.get("template")
                            or "Shablon moderatsiyaga yuborildi."
                        ),
                        "data": data,
                        "url": url,
                    }

    # Hech qaysi API topilmasa — aniq yo‘riqnoma
    return {
        "ok": False,
        "error": (
            "Shablon avtomatik yuborilmadi. DevSMS kabinetida "
            "Шаблоны → Отправить шаблон yoki my.eskiz.uz → СМС → Мои тексты "
            "orqali quyidagi matnni qo‘shing va tasdiqlang."
        ),
        "template": text,
        "attempts": attempts[:6],
    }


def send_dev_sms(
    *,
    phone: str,
    message: str,
    token: str | None = None,
    sender: str | None = None,
    sms_type: str | None = None,
) -> dict:
    """
    TezPOS sendDevSms — to'g'ridan-to'g'ri DevSMS API.
    Qaytaradi: {ok, error?, phone?, sms_id?, status?, template_submit?}
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

    preferred = (sms_type or getattr(settings, "DEVSMS_TYPE", "") or "simple").strip().lower()
    # simple — tezroq (moderatsiyasiz kanal); eskiz — brend/4546, shablon kerak
    type_order: list[str] = []
    for t in (preferred, "simple", "eskiz"):
        if t and t not in type_order:
            type_order.append(t)

    from_id = (sender if sender is not None else getattr(settings, "DEVSMS_FROM", "") or "").strip()
    last_err = ""

    for t in type_order:
        body: dict = {"phone": to, "message": text, "type": t}
        if from_id and t != "simple":
            body["from"] = from_id
        res = _api_post(DEVSMS_URL, auth, body)
        data = res.get("data") or {}
        if res.get("http") == 200 and data.get("success"):
            payload = data.get("data") if isinstance(data.get("data"), dict) else {}
            return {
                "ok": True,
                "phone": to,
                "sms_id": payload.get("sms_id"),
                "status": payload.get("status") or "sent",
                "cost": payload.get("total_cost"),
                "balance": payload.get("balance"),
                "type": t,
            }
        last_err = str(data.get("error") or data.get("message") or f"DevSMS xato ({res.get('http')})")
        if not _is_moderation_error(last_err):
            # Boshqa xato — keyingi typega o‘tmasdan qaytarish shart emas; simple/eskiz farqi uchun davom
            if "balance" in last_err.lower() or "баланс" in last_err.lower() or "token" in last_err.lower():
                return {"ok": False, "error": last_err}

    # Moderatsiya: shablonni yuborib, foydalanuvchiga tushunarli javob
    tpl_res = None
    if _is_moderation_error(last_err):
        tpl_res = submit_template(text, token=auth)
        hint = (
            "SMS matni Eskiz moderatsiyasidan o‘tmagan. "
            "Namuna shablon moderatsiyaga yuborildi — tasdiqlangach qayta urinib ko‘ring."
            if tpl_res.get("ok")
            else (
                "SMS matni Eskiz moderatsiyasidan o‘tmagan. "
                + str(tpl_res.get("error") or "")
            )
        )
        return {
            "ok": False,
            "error": hint,
            "provider_error": last_err,
            "template_submit": tpl_res,
            "template": text,
        }

    return {"ok": False, "error": last_err or "SMS yuborilmadi"}
