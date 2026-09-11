"""
Qarz eslatmalari — Telethon (user session) orqali Telegramga yuborish.

Muhim: ImportContacts FloodWait oldini olish uchun
bitta ulanish + batch + kutish.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from django.conf import settings
from django.utils import timezone

from . import devsms
from .models import ClientDebtor, DebtSmsTemplate

logger = logging.getLogger(__name__)

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

BATCH_SIZE = 8
BATCH_PAUSE_SEC = 4.0
PLACEHOLDER_NAMES = {"tezpos", "qarz", "mijoz", "tp"}


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


def _run(coro, timeout: int = 900):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=timeout)
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


def _user_display(u) -> tuple[str, str]:
    username = (getattr(u, "username", None) or "") or ""
    first = (getattr(u, "first_name", None) or "") or ""
    last = (getattr(u, "last_name", None) or "") or ""
    full = (first + " " + last).strip()
    low = full.lower()
    if any(x in low for x in PLACEHOLDER_NAMES) or not full:
        display = ("@" + username) if username else "Telegram bor"
    else:
        display = ("@" + username) if username else full
    return username, display


async def _import_batch(client, items: list[tuple[int, str, str]]) -> dict[int, dict]:
    """
    items: [(client_id, phone_normalized, label_name), ...]
    returns: {client_id: meta}
    """
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.contacts import (
        DeleteContactsRequest,
        ImportContactsRequest,
    )
    from telethon.tl.types import InputPhoneContact, InputUser

    out: dict[int, dict] = {}
    if not items:
        return out

    contacts = []
    for cid, phone_n, label in items:
        intl = "+" + phone_n if not str(phone_n).startswith("+") else str(phone_n)
        # Import uchun qisqa label (Telegram contact name)
        nm = (label or "Mijoz").strip()[:40] or "Mijoz"
        contacts.append(
            InputPhoneContact(
                client_id=cid,
                phone=intl,
                first_name=nm,
                last_name="",
            )
        )

    while True:
        try:
            result = await client(ImportContactsRequest(contacts))
            break
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 0) or 0) + 2
            logger.warning("FloodWait %ss — kutilyapti", wait)
            await asyncio.sleep(wait)

    # retry_contacts / users mapping
    users_by_id = {u.id: u for u in (result.users or [])}
    imported = list(getattr(result, "imported", []) or [])
    # imported: ImportContact {user_id, client_id}
    for imp in imported:
        cid = int(getattr(imp, "client_id", 0) or 0)
        uid = int(getattr(imp, "user_id", 0) or 0)
        u = users_by_id.get(uid)
        if not u:
            out[cid] = {
                "ok": False,
                "status": "no_telegram",
                "telegram_id": "",
                "username": "",
                "display_name": "",
                "error": "Telegram yo‘q",
            }
            continue
        username, display = _user_display(u)
        out[cid] = {
            "ok": True,
            "status": "ok",
            "telegram_id": str(u.id),
            "username": username,
            "display_name": display,
            "error": "",
            "_user": u,
        }

    # retry_contacts — raqam Telegramda yo‘q
    for rc in list(getattr(result, "retry_contacts", []) or []):
        try:
            cid = int(rc)
        except (TypeError, ValueError):
            continue
        if cid not in out:
            out[cid] = {
                "ok": False,
                "status": "no_telegram",
                "telegram_id": "",
                "username": "",
                "display_name": "",
                "error": "Bu raqamda Telegram yo‘q",
            }

    # Import qilinmaganlar
    for cid, _, _ in items:
        if cid not in out:
            out[cid] = {
                "ok": False,
                "status": "no_telegram",
                "telegram_id": "",
                "username": "",
                "display_name": "",
                "error": "Bu raqamda Telegram yo‘q",
            }

    # Kontaktlarni o‘chirish
    to_del = []
    for meta in out.values():
        u = meta.pop("_user", None)
        if u is not None:
            to_del.append(InputUser(u.id, u.access_hash))
    if to_del:
        try:
            await client(DeleteContactsRequest(to_del))
        except FloodWaitError as exc:
            await asyncio.sleep(int(getattr(exc, "seconds", 0) or 0) + 1)
        except Exception:
            pass

    return out


async def resolve_debtors_batch_async(
    debtors: list[ClientDebtor], *, force: bool = False
) -> list[ClientDebtor]:
    if not telethon_configured():
        return debtors

    need: list[ClientDebtor] = []
    for d in debtors:
        if not (d.phone or "").strip():
            d.telegram_status = "no_phone"
            d.save(update_fields=["telegram_status", "updated_at"])
            continue
        checked = d.telegram_checked_at
        fresh = (
            checked
            and (timezone.now() - checked) < timedelta(days=7)
            and d.telegram_status in ("ok", "no_telegram")
        )
        if fresh and not force:
            continue
        need.append(d)

    if not need:
        return debtors

    client = await _client()
    try:
        for i in range(0, len(need), BATCH_SIZE):
            chunk = need[i : i + BATCH_SIZE]
            items = []
            for idx, d in enumerate(chunk):
                phone_n = devsms.normalize_phone(d.phone)
                if not phone_n:
                    d.telegram_status = "no_phone"
                    d.save(update_fields=["telegram_status", "updated_at"])
                    continue
                # client_id > 0 unique in batch
                items.append((idx + 1, phone_n, (d.name or "Mijoz")[:40]))

            if not items:
                continue

            mapping = await _import_batch(client, items)
            for cid, phone_n, _label in items:
                # find debtor by phone in chunk
                meta = mapping.get(cid) or {
                    "ok": False,
                    "status": "error",
                    "telegram_id": "",
                    "username": "",
                    "display_name": "",
                    "error": "Noma’lum",
                }
                # map client_id back to debtor: items order == chunk filtered
                # rebuild: items only has those with phone
                pass

            # Reliable mapping: zip filtered debtors with items
            phone_debtors = []
            for d in chunk:
                phone_n = devsms.normalize_phone(d.phone)
                if phone_n:
                    phone_debtors.append(d)
            for (cid, _p, _l), d in zip(items, phone_debtors):
                meta = mapping.get(cid) or {
                    "ok": False,
                    "status": "error",
                    "telegram_id": "",
                    "username": "",
                    "display_name": "",
                    "error": "Noma’lum",
                }
                apply_telegram_meta(d, meta)

            if i + BATCH_SIZE < len(need):
                await asyncio.sleep(BATCH_PAUSE_SEC)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return debtors


async def _send_many_async(messages: list[tuple[str, str]]) -> list[dict]:
    """messages: [(telegram_id, text), ...]"""
    from telethon.errors import FloodWaitError

    if not messages:
        return []
    client = await _client()
    results = []
    try:
        for tid, text in messages:
            try:
                await client.send_message(int(tid), text)
                results.append({"ok": True, "error": "", "telegram_id": tid})
                await asyncio.sleep(1.2)
            except FloodWaitError as exc:
                wait = int(getattr(exc, "seconds", 0) or 0) + 2
                await asyncio.sleep(wait)
                try:
                    await client.send_message(int(tid), text)
                    results.append({"ok": True, "error": "", "telegram_id": tid})
                except Exception as e2:
                    results.append({"ok": False, "error": str(e2)[:200], "telegram_id": tid})
            except Exception as exc:
                results.append({"ok": False, "error": str(exc)[:200], "telegram_id": tid})
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return results


def resolve_debtors_batch(
    debtors: Iterable[ClientDebtor], *, force: bool = False
) -> list[ClientDebtor]:
    rows = list(debtors)
    return _run(resolve_debtors_batch_async(rows, force=force), timeout=1800)


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
    resolve_debtors_batch([debtor], force=force)
    debtor.refresh_from_db()
    return debtor


def telegram_display(debtor: ClientDebtor) -> str:
    st = debtor.telegram_status or ""
    if st == "ok":
        if debtor.telegram_username:
            return "@" + debtor.telegram_username.lstrip("@")
        if debtor.telegram_name and debtor.telegram_name.lower() not in PLACEHOLDER_NAMES:
            if "tezpos" not in debtor.telegram_name.lower():
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
    if debtor.telegram_status != "ok" or not debtor.telegram_id:
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
    results = _run(_send_many_async([(debtor.telegram_id, text)]))
    res = results[0] if results else {"ok": False, "error": "Yuborilmadi"}
    if res.get("ok"):
        debtor.tg_last_remind_kind = kind
        debtor.tg_last_remind_date = timezone.localdate()
        debtor.save(
            update_fields=["tg_last_remind_kind", "tg_last_remind_date", "updated_at"]
        )
    res["display"] = telegram_display(debtor)
    res["text_preview"] = text[:120]
    return res


def send_debt_reminders_batch(jobs: list[tuple[ClientDebtor, str]]) -> list[dict]:
    """jobs: [(debtor, kind), ...] — bitta client bilan yuboradi."""
    prepared: list[tuple[ClientDebtor, str, str]] = []
    for debtor, kind in jobs:
        bal = debtor.balance()
        if bal <= 0 or not debtor.due_date:
            continue
        if debtor.telegram_status != "ok" or not debtor.telegram_id:
            continue
        overdue_days = max(0, (timezone.localdate() - debtor.due_date).days)
        text = build_reminder_text(
            kind=kind,
            shop=_shop_short(debtor.shop_key),
            amount=bal,
            due=debtor.due_date,
            overdue_days=overdue_days,
        )
        prepared.append((debtor, kind, text))

    results = _run(
        _send_many_async([(d.telegram_id, t) for d, _k, t in prepared]),
        timeout=1800,
    )
    out = []
    for (debtor, kind, text), res in zip(prepared, results):
        if res.get("ok"):
            debtor.tg_last_remind_kind = kind
            debtor.tg_last_remind_date = timezone.localdate()
            debtor.save(
                update_fields=[
                    "tg_last_remind_kind",
                    "tg_last_remind_date",
                    "updated_at",
                ]
            )
        res = dict(res)
        res["debtor_id"] = debtor.pk
        res["name"] = debtor.name
        res["display"] = telegram_display(debtor)
        out.append(res)
    return out
