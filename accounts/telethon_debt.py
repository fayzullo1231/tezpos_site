"""
Qarz eslatmalari — Telethon (user session) orqali Telegramga yuborish.

Env:
  TELETHON_API_ID
  TELETHON_API_HASH
  TELETHON_SESSION   (StringSession)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.utils import timezone

from . import devsms
from .models import ClientDebtor, DebtSmsTemplate

logger = logging.getLogger(__name__)

# 1 kun oldin / bugun / muddati o'tgan
KIND_SOON = "soon"
KIND_DUE_TODAY = "due_today"
KIND_OVERDUE = "overdue"

MSG_DUE_TODAY = (
    "Assalomu alaykum! Hurmatli mijoz, {shop}dagi {amount} so‘mlik qarzingizni "
    "bugun, {date} kuni qaytarish muddati belgilangan. Iltimos, qarzingizni "
    "o‘z vaqtida to‘lashni unutmang."
)

MSG_SOON = (
    "Eslatma! Hurmatli mijoz, {shop}dagi {amount} so‘mlik qarzingizni qaytarish "
    "muddati {date} kuni. To‘lovni o‘z vaqtida amalga oshirishingizni so‘raymiz."
)

MSG_OVERDUE = (
    "Hurmatli mijoz! Sizning {shop}dagi {amount} so‘mlik qarzingizni to‘lash "
    "muddati {date} kuni tugagan. Hozirda to‘lov {days} kun kechikkan. "
    "Iltimos, qarzingizni imkon qadar tezroq to‘lashingizni so‘raymiz."
)


def _fmt_amount(value) -> str:
    try:
        n = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        n = Decimal("0")
    return f"{abs(n):,.0f}".replace(",", " ")


def _shop_short(shop_key: str) -> str:
    tpl = DebtSmsTemplate.objects.filter(shop_key=shop_key).first()
    label = (tpl.shop_label if tpl else "") or ""
    label = label.strip() or "Kulol Optom"
    if " - " in label:
        return label.split(" - ", 1)[0].strip() or "Kulol Optom"
    if "-" in label[1:]:
        return label.split("-", 1)[0].strip() or "Kulol Optom"
    return label


def build_reminder_text(
    *,
    kind: str,
    shop: str,
    amount,
    due: date,
    overdue_days: int = 0,
) -> str:
    amount_s = _fmt_amount(amount)
    date_s = due.strftime("%d.%m.%Y")
    shop_s = (shop or "Kulol Optom").strip()
    if kind == KIND_DUE_TODAY:
        return MSG_DUE_TODAY.format(shop=shop_s, amount=amount_s, date=date_s)
    if kind == KIND_SOON:
        return MSG_SOON.format(shop=shop_s, amount=amount_s, date=date_s)
    return MSG_OVERDUE.format(
        shop=shop_s, amount=amount_s, date=date_s, days=max(1, int(overdue_days))
    )


def reminder_kind_for(due: date | None, today: date | None = None) -> str | None:
    if not due:
        return None
    today = today or timezone.localdate()
    delta = (due - today).days
    if delta == 1:
        return KIND_SOON
    if delta == 0:
        return KIND_DUE_TODAY
    if delta < 0:
        return KIND_OVERDUE
    return None


def telethon_configured() -> bool:
    api_id = str(getattr(settings, "TELETHON_API_ID", "") or "").strip()
    api_hash = str(getattr(settings, "TELETHON_API_HASH", "") or "").strip()
    session = str(getattr(settings, "TELETHON_SESSION", "") or "").strip()
    return bool(api_id and api_hash and session and api_id.isdigit())


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=90)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(str(settings.TELETHON_API_ID).strip())
    api_hash = str(settings.TELETHON_API_HASH).strip()
    session = str(settings.TELETHON_SESSION).strip()
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session avtorizatsiyadan o‘tmagan")
    return client


async def _resolve_phone_async(phone: str) -> dict[str, Any]:
    """
    returns:
      ok, status (ok|no_telegram|error), telegram_id, username, display_name, error
    """
    phone_n = devsms.normalize_phone(phone)
    empty = {
        "ok": False,
        "status": "error",
        "telegram_id": "",
        "username": "",
        "display_name": "",
        "error": "",
    }
    if not phone_n:
        empty["status"] = "no_phone"
        empty["error"] = "Telefon yo‘q"
        return empty
    if not telethon_configured():
        empty["error"] = "Telethon sozlanmagan (API_ID/HASH/SESSION)"
        return empty

    from telethon.tl.functions.contacts import (
        DeleteContactsRequest,
        ImportContactsRequest,
    )
    from telethon.tl.types import InputPhoneContact, InputUser

    client = None
    try:
        client = await _client()
        # +998...
        intl = "+" + phone_n if not phone_n.startswith("+") else phone_n
        result = await client(
            ImportContactsRequest(
                [
                    InputPhoneContact(
                        client_id=0,
                        phone=intl,
                        first_name="TezPOS",
                        last_name="Qarz",
                    )
                ]
            )
        )
        users = list(result.users or [])
        if not users:
            return {
                "ok": False,
                "status": "no_telegram",
                "telegram_id": "",
                "username": "",
                "display_name": "",
                "error": "Bu raqamda Telegram yo‘q",
            }
        u = users[0]
        username = (getattr(u, "username", None) or "") or ""
        first = (getattr(u, "first_name", None) or "") or ""
        last = (getattr(u, "last_name", None) or "") or ""
        display = (first + " " + last).strip() or username or phone_n
        # kontaktni tozalash (spam bo‘lmasin)
        try:
            await client(
                DeleteContactsRequest([InputUser(u.id, u.access_hash)])
            )
        except Exception:
            pass
        return {
            "ok": True,
            "status": "ok",
            "telegram_id": str(u.id),
            "username": username,
            "display_name": display,
            "error": "",
        }
    except Exception as exc:
        logger.exception("telethon resolve failed")
        empty["error"] = str(exc)[:200]
        return empty
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


async def _send_to_user_async(telegram_id: str, text: str) -> dict[str, Any]:
    if not telethon_configured():
        return {"ok": False, "error": "Telethon sozlanmagan"}
    client = None
    try:
        client = await _client()
        await client.send_message(int(telegram_id), text)
        return {"ok": True, "error": ""}
    except Exception as exc:
        logger.exception("telethon send failed")
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


def resolve_telegram_for_phone(phone: str) -> dict[str, Any]:
    return _run(_resolve_phone_async(phone))


def send_telegram_message(telegram_id: str, text: str) -> dict[str, Any]:
    return _run(_send_to_user_async(str(telegram_id), text))


def apply_telegram_meta(debtor: ClientDebtor, meta: dict) -> None:
    debtor.telegram_status = meta.get("status") or "error"
    debtor.telegram_id = meta.get("telegram_id") or ""
    debtor.telegram_username = meta.get("username") or ""
    debtor.telegram_name = meta.get("display_name") or ""
    debtor.telegram_checked_at = timezone.now()
    debtor.save(
        update_fields=[
            "telegram_status",
            "telegram_id",
            "telegram_username",
            "telegram_name",
            "telegram_checked_at",
            "updated_at",
        ]
    )


def ensure_telegram_resolved(debtor: ClientDebtor, *, force: bool = False) -> ClientDebtor:
    """Telefon bo‘yicha Telegramni aniqlash (kesh bilan)."""
    if not debtor.phone:
        debtor.telegram_status = "no_phone"
        debtor.save(update_fields=["telegram_status", "updated_at"])
        return debtor
    checked = debtor.telegram_checked_at
    fresh = (
        checked
        and (timezone.now() - checked) < timedelta(days=7)
        and debtor.telegram_status in ("ok", "no_telegram")
    )
    if fresh and not force and debtor.telegram_id:
        return debtor
    meta = resolve_telegram_for_phone(debtor.phone)
    apply_telegram_meta(debtor, meta)
    return debtor


def telegram_display(debtor: ClientDebtor) -> str:
    st = debtor.telegram_status or ""
    if st == "ok":
        if debtor.telegram_username:
            return "@" + debtor.telegram_username.lstrip("@")
        if debtor.telegram_name:
            return debtor.telegram_name
        return "Telegram bor"
    if st == "no_telegram":
        return "Telegram yo‘q"
    if st == "no_phone":
        return "Telefon yo‘q"
    if st == "error":
        return "Telegram xato"
    return "Tekshirilmagan"


def send_debt_reminder(debtor: ClientDebtor, kind: str) -> dict[str, Any]:
    bal = debtor.balance()
    if bal <= 0 or not debtor.due_date:
        return {"ok": False, "skipped": True, "error": "Qarz yoki sana yo‘q"}
    ensure_telegram_resolved(debtor)
    if debtor.telegram_status != "ok" or not debtor.telegram_id:
        return {
            "ok": False,
            "skipped": True,
            "status": debtor.telegram_status,
            "error": "Telegram yo‘q yoki topilmadi",
            "display": telegram_display(debtor),
        }
    overdue_days = max(0, (timezone.localdate() - debtor.due_date).days)
    text = build_reminder_text(
        kind=kind,
        shop=_shop_short(debtor.shop_key),
        amount=bal,
        due=debtor.due_date,
        overdue_days=overdue_days,
    )
    res = send_telegram_message(debtor.telegram_id, text)
    if res.get("ok"):
        debtor.tg_last_remind_kind = kind
        debtor.tg_last_remind_date = timezone.localdate()
        debtor.save(
            update_fields=["tg_last_remind_kind", "tg_last_remind_date", "updated_at"]
        )
    res["display"] = telegram_display(debtor)
    res["text_preview"] = text[:120]
    return res
