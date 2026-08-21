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


def _normalize_sms_text(text: str) -> str:
    """Eskiz GSM-7 uchun: curly apostrof → ASCII, CR/LF tozalash."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    for ch in ("\u2018", "\u2019", "\u02bc", "\u0060", "\u00b4"):
        t = t.replace(ch, "'")
    # ortiqcha bo‘sh qatorlarni qisqartirish
    lines = [ln.rstrip() for ln in t.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def build_debt_message(
    *,
    shop: str,
    branch: str = "",
    debt_amount,
    balance,
    check_link: str = "",
) -> str:
    """
    Tasdiqlangan Eskiz/DevSMS shablon (ASCII apostrof — 1 SMS qism):
      Kulol Optom-Oziq ovqat:
      Qarzdorligingiz : 1 456 000 so'm.
      Iltimos, qarzdorlikni to'lashni unutmang.
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
    text = (
        f"{head}:\n"
        f"Qarzdorligingiz : {_fmt_som(show)} so'm.\n"
        f"Iltimos, qarzdorlikni to'lashni unutmang."
    )
    return _normalize_sms_text(text)


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
    DevSMS hujjat + TezPOS: { phone, message, from }.
    Qaytaradi: {ok, error?, phone?, sms_id?, status?}
    """
    auth = resolve_token(token)
    if not auth:
        return {"ok": False, "error": "DevSMS token yo‘q (DEVSMS_TOKEN yoki TezPOS token fayli)."}

    to = normalize_phone(phone)
    if not to:
        return {"ok": False, "error": "Mijoz telefon raqami yo‘q yoki noto‘g‘ri."}

    text = _normalize_sms_text(message or "")
    if not text:
        return {"ok": False, "error": "SMS matni bo‘sh."}

    # Hujjat: from default 4546 (TezPOS ham shunday yuboradi)
    from_id = (
        sender
        if sender is not None
        else (getattr(settings, "DEVSMS_FROM", None) or "4546")
    )
    from_id = str(from_id or "4546").strip() or "4546"

    body: dict = {"phone": to, "message": text, "from": from_id}
    # type faqat aniq berilsa (odatda kerak emas — TezPOS yubormaydi)
    t = (sms_type if sms_type is not None else getattr(settings, "DEVSMS_TYPE", "") or "").strip()
    if t and t not in ("auto", "default", "eskiz"):
        body["type"] = t
    elif t == "eskiz":
        body["type"] = "eskiz"

    res = _api_post(DEVSMS_URL, auth, body)
    data = res.get("data") or {}
    if res.get("http") == 200 and data.get("success"):
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        return {
            "ok": True,
            "phone": to,
            "sms_id": payload.get("sms_id"),
            "request_id": payload.get("request_id"),
            "status": payload.get("status") or "sent",
            "cost": payload.get("total_cost"),
            "balance": payload.get("balance"),
            "parts": payload.get("parts_count"),
            "type": payload.get("type") or "eskiz",
            "preview": text,
        }

    last_err = str(data.get("error") or data.get("message") or f"DevSMS xato ({res.get('http')})")
    if _is_moderation_error(last_err):
        tpl_res = submit_template(text, token=auth)
        return {
            "ok": False,
            "error": (
                "SMS matni Eskizda tasdiqlanmagan. "
                "my.eskiz.uz → СМС → Мои тексты ga shu matnni qo‘shing "
                "(raqam o‘rniga istalgan summa bo‘lishi mumkin), tasdiqlangach qayta yuboring."
            ),
            "provider_error": last_err,
            "template_submit": tpl_res,
            "template": text,
        }
    return {"ok": False, "error": last_err, "template": text}
