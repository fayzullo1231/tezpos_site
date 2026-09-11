"""
Qarz eslatmalari — Telethon (user session) orqali Telegramga yuborish.

Kontaktga saqlanmaydi: contacts.resolvePhone (ImportContacts yo‘q).
Har safar raqam bo‘yicha qidiriladi, keyin xabar yuboriladi.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from asgiref.sync import sync_to_async
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
PLACEHOLDER_NAMES = {"tezpos", "qarz", "mijoz", "tp"}

# Telegram: resolvePhone — max 1 so‘rov ~3 soniyada
RESOLVE_PAUSE_SEC = 3.5
SEND_PAUSE_SEC = 1.5

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def format_text_for_telegram(text: str) -> str:
    """
    DevSMS matnini Telegram HTML ga:
    - do‘kon nomi qalin
    - URL lar bosiladigan <a>
    - Chek link bir qatorda
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\u200b", "").replace("\u200c", "").replace("\ufeff", "")
    # "Chek:\nhttps://..." → "Chek : https://..."
    raw = re.sub(
        r"(?im)^Chek\s*:\s*\n+(https?://\S+)",
        r"Chek : \1",
        raw,
    )
    raw = re.sub(r"(?im)^Chek\s*:\s*(https?://)", r"Chek : \1", raw)

    lines = raw.split("\n")
    out_lines: list[str] = []
    headed = False
    for line in lines:
        if not headed and line.strip():
            out_lines.append(f"<b>{html.escape(line.strip())}</b>")
            headed = True
            continue
        m = _URL_RE.search(line)
        if m:
            before = line[: m.start()]
            url = m.group(0).rstrip(".,);]")
            url_clean = "".join(ch for ch in url if ord(ch) < 128)
            after = line[m.end() :]
            chunk = html.escape(before)
            if url_clean.startswith("http"):
                chunk += (
                    f'<a href="{html.escape(url_clean, quote=True)}">'
                    f"{html.escape(url_clean)}</a>"
                )
            else:
                chunk += html.escape(url)
            chunk += html.escape(after)
            out_lines.append(chunk)
        else:
            out_lines.append(html.escape(line))
    return "<br/>".join(out_lines)


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
    """Sync kontekstda async ishlatish (manage.py / view)."""

    async def _wrapped():
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_wrapped())

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _wrapped()).result(timeout=timeout + 30)


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


def _intl_phone(phone_n: str) -> str:
    p = (phone_n or "").strip()
    if not p:
        return ""
    return p if p.startswith("+") else ("+" + p)


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


def _meta_ok(u) -> dict:
    username, display = _user_display(u)
    return {
        "ok": True,
        "status": "ok",
        "telegram_id": str(u.id),
        "username": username,
        "display_name": display,
        "error": "",
        "user": u,
    }


def _meta_no_tg(err: str = "Bu raqamda Telegram yo‘q") -> dict:
    return {
        "ok": False,
        "status": "no_telegram",
        "telegram_id": "",
        "username": "",
        "display_name": "",
        "error": err if err != "resolve_privacy" else "Bu raqamda Telegram yo‘q",
        "user": None,
        "_privacy": err == "resolve_privacy",
    }


def _meta_err(err: str) -> dict:
    return {
        "ok": False,
        "status": "error",
        "telegram_id": "",
        "username": "",
        "display_name": "",
        "error": (err or "Xato")[:200],
        "user": None,
    }


async def _resolve_phone(client, phone_n: str) -> dict:
    """
    Raqamni Telegram userga aylantiradi.
    Avvalo ResolvePhone (kontaktga qo‘shilmaydi).
    """
    from telethon.errors import FloodWaitError, RPCError
    from telethon.tl.functions.contacts import ResolvePhoneRequest

    intl = _intl_phone(phone_n)
    if not intl:
        return _meta_err("Telefon yo‘q")

    while True:
        try:
            result = await client(ResolvePhoneRequest(phone=intl))
            users = list(getattr(result, "users", None) or [])
            if not users:
                return _meta_no_tg()
            return _meta_ok(users[0])
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 0) or 0) + 2
            logger.warning("FloodWait %ss — kutilyapti", wait)
            await asyncio.sleep(wait)
        except RPCError as exc:
            msg = (getattr(exc, "message", None) or str(exc) or "").upper()
            if "PHONE_NOT_OCCUPIED" in msg or "PHONE_NOT_OCCUPIED" in str(exc):
                # Privacy: resolvePhone yopiq bo‘lishi mumkin — import fallback
                return _meta_no_tg("resolve_privacy")
            logger.exception("telethon resolvePhone failed")
            return _meta_err(str(exc))
        except Exception as exc:
            logger.exception("telethon resolvePhone failed")
            return _meta_err(str(exc))


async def _import_user_by_phone(client, phone_n: str) -> dict:
    """
    Vaqtinchalik ImportContacts — user qaytaradi.
    Kontaktni o‘chirish yuborishdan KEYIN (_delete_temp_contact).
    """
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    intl = _intl_phone(phone_n)
    if not intl:
        return _meta_err("Telefon yo‘q")

    contact = InputPhoneContact(
        client_id=random.randint(1, 0x7FFFFFFF),
        phone=intl,
        first_name="TP",
        last_name="",
    )
    while True:
        try:
            result = await client(ImportContactsRequest([contact]))
            break
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 0) or 0) + 2
            logger.warning("FloodWait import %ss", wait)
            await asyncio.sleep(wait)

    users = list(getattr(result, "users", None) or [])
    imported = list(getattr(result, "imported", None) or [])
    user = None
    if imported and users:
        uid = int(getattr(imported[0], "user_id", 0) or 0)
        user = next((u for u in users if int(u.id) == uid), users[0])
    elif users:
        user = users[0]

    if user is None:
        return _meta_no_tg()
    meta = _meta_ok(user)
    meta["_imported"] = True
    return meta


async def _delete_temp_contact(client, user) -> None:
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.contacts import DeleteContactsRequest
    from telethon.tl.types import InputUser

    if user is None:
        return
    try:
        await client(
            DeleteContactsRequest(
                [InputUser(int(user.id), int(user.access_hash or 0))]
            )
        )
    except FloodWaitError as exc:
        await asyncio.sleep(int(getattr(exc, "seconds", 0) or 0) + 1)
    except Exception:
        logger.warning("DeleteContacts failed (ignore)", exc_info=True)


async def _resolve_phone_for_send(client, phone_n: str) -> dict:
    """Yuborish uchun: ResolvePhone, bo‘lmasa Import (delete keyinroq)."""
    meta = await _resolve_phone(client, phone_n)
    if meta.get("ok") and meta.get("user") is not None:
        return meta
    return await _import_user_by_phone(client, phone_n)


@sync_to_async
def _save_status(debtor: ClientDebtor, status: str) -> None:
    debtor.telegram_status = status
    debtor.save(update_fields=["telegram_status", "updated_at"])


@sync_to_async
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


@sync_to_async
def _mark_reminded(debtor: ClientDebtor, kind: str) -> None:
    debtor.tg_last_remind_kind = kind
    debtor.tg_last_remind_date = timezone.localdate()
    debtor.save(
        update_fields=["tg_last_remind_kind", "tg_last_remind_date", "updated_at"]
    )


async def resolve_debtors_batch_async(
    debtors: list[ClientDebtor], *, force: bool = False
) -> list[ClientDebtor]:
    if not telethon_configured():
        return debtors

    need: list[ClientDebtor] = []
    for d in debtors:
        if not (d.phone or "").strip():
            await _save_status(d, "no_phone")
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
        for i, d in enumerate(need):
            phone_n = await sync_to_async(devsms.normalize_phone)(d.phone)
            if not phone_n:
                await _save_status(d, "no_phone")
                continue
            meta = await _resolve_phone_for_send(client, phone_n)
            user = meta.pop("user", None)
            imported = bool(meta.pop("_imported", False))
            meta.pop("_privacy", None)
            if imported:
                await _delete_temp_contact(client, user)
            await apply_telegram_meta(d, meta)
            if i + 1 < len(need):
                await asyncio.sleep(RESOLVE_PAUSE_SEC)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return debtors


async def _send_by_phone(
    client, phone_n: str, text: str, *, prefer_telegram_id: str = ""
) -> dict:
    """
    DevSMS bilan bir xil matnni Telegramga yuboradi.
    Tartib: topish → yuborish → (agar import qilingan bo‘lsa) kontaktni o‘chirish.
    """
    from telethon.errors import FloodWaitError

    user = None
    meta: dict = {}
    imported_temp = False

    tid = str(prefer_telegram_id or "").strip()
    if tid.isdigit():
        try:
            entity = await client.get_entity(int(tid))
            user = entity
            meta = _meta_ok(entity)
        except Exception:
            user = None

    if user is None:
        meta = await _resolve_phone_for_send(client, phone_n)
        user = meta.get("user")
        imported_temp = bool(meta.get("_imported"))

    clean_meta = {
        k: v for k, v in meta.items() if k not in ("user", "_privacy", "_imported")
    }
    if not meta.get("ok") or user is None:
        return {
            "ok": False,
            "error": meta.get("error") or "Telegram topilmadi",
            "status": meta.get("status") or "error",
            "meta": clean_meta,
        }

    send_ok = False
    send_err = ""
    while True:
        try:
            await client.send_message(
                user,
                format_text_for_telegram(text),
                parse_mode="html",
                link_preview=True,
            )
            send_ok = True
            break
        except FloodWaitError as exc:
            wait = int(getattr(exc, "seconds", 0) or 0) + 2
            logger.warning("FloodWait send %ss", wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            logger.exception("telethon send_message failed")
            send_err = str(exc)[:200]
            break

    # Muhim: yuborgandan KEYIN o‘chirish (maxfiylik / kontaktga saqlamaslik)
    if imported_temp:
        await _delete_temp_contact(client, user)

    if not send_ok:
        return {
            "ok": False,
            "error": send_err or "Yuborilmadi",
            "status": "error",
            "meta": clean_meta,
        }

    return {
        "ok": True,
        "error": "",
        "status": "ok",
        "telegram_id": str(getattr(user, "id", "") or ""),
        "meta": clean_meta,
    }


async def send_jobs_by_phone_async(
    jobs: list[tuple[ClientDebtor, str, str]],
) -> list[dict]:
    """jobs: [(debtor, kind, text), ...] — har biri raqam orqali."""
    if not jobs:
        return []
    client = await _client()
    out: list[dict] = []
    try:
        for i, (debtor, kind, text) in enumerate(jobs):
            phone_n = await sync_to_async(devsms.normalize_phone)(debtor.phone)
            if not phone_n:
                out.append(
                    {
                        "ok": False,
                        "error": "Telefon yo‘q",
                        "debtor_id": debtor.pk,
                        "name": debtor.name,
                    }
                )
                continue
            res = await _send_by_phone(
                client,
                phone_n,
                text,
                prefer_telegram_id=str(getattr(debtor, "telegram_id", "") or ""),
            )
            meta = res.get("meta") or {}
            if meta:
                await apply_telegram_meta(debtor, meta)
            if res.get("ok"):
                await _mark_reminded(debtor, kind)
            row = {
                "ok": bool(res.get("ok")),
                "error": res.get("error") or "",
                "telegram_id": res.get("telegram_id") or meta.get("telegram_id") or "",
                "debtor_id": debtor.pk,
                "name": debtor.name,
                "display": await sync_to_async(telegram_display)(debtor),
            }
            out.append(row)
            if i + 1 < len(jobs):
                await asyncio.sleep(max(SEND_PAUSE_SEC, RESOLVE_PAUSE_SEC))
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return out


def resolve_debtors_batch(
    debtors: Iterable[ClientDebtor], *, force: bool = False
) -> list[ClientDebtor]:
    rows = list(debtors)
    return _run(resolve_debtors_batch_async(rows, force=force), timeout=3600)


def send_text_by_phone(
    phone: str, text: str, *, debtor: ClientDebtor | None = None
) -> dict[str, Any]:
    """
    DevSMS shabloni matnini Telegramga yuboradi (bir xil text).
    Kontaktga saqlanmaydi.
    """
    if not telethon_configured():
        return {"ok": False, "error": "Telethon sozlanmagan"}
    phone_n = devsms.normalize_phone(phone)
    if not phone_n:
        return {"ok": False, "error": "Telefon yo‘q"}
    msg = (text or "").strip()
    if not msg:
        return {"ok": False, "error": "Matn bo‘sh"}
    prefer_id = ""
    if debtor is not None:
        prefer_id = str(getattr(debtor, "telegram_id", "") or "")

    async def _one():
        client = await _client()
        try:
            res = await _send_by_phone(
                client, phone_n, msg, prefer_telegram_id=prefer_id
            )
            meta = res.get("meta") or {}
            if debtor is not None and meta:
                await apply_telegram_meta(debtor, meta)
            out = {
                "ok": bool(res.get("ok")),
                "error": res.get("error") or "",
                "telegram_id": res.get("telegram_id") or "",
                "status": res.get("status") or "",
                "channel": "telegram",
            }
            if not out["ok"]:
                logger.warning(
                    "Telegram qarz xabari yuborilmadi phone=%s err=%s",
                    phone_n,
                    out["error"],
                )
            return out
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    try:
        return _run(_one(), timeout=180)
    except Exception as exc:
        logger.exception("send_text_by_phone failed")
        return {"ok": False, "error": str(exc)[:200], "channel": "telegram"}


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
    overdue_days = max(0, (timezone.localdate() - debtor.due_date).days)
    text = build_reminder_text(
        kind=kind,
        shop=_shop_short(debtor.shop_key),
        amount=bal,
        due=debtor.due_date,
        overdue_days=overdue_days,
    )
    results = _run(send_jobs_by_phone_async([(debtor, kind, text)]))
    res = results[0] if results else {"ok": False, "error": "Yuborilmadi"}
    debtor.refresh_from_db()
    res["display"] = telegram_display(debtor)
    res["text_preview"] = text[:120]
    return res


def send_debt_reminders_batch(jobs: list[tuple[ClientDebtor, str]]) -> list[dict]:
    """jobs: [(debtor, kind), ...] — har safar raqam bilan qidirib yuboradi."""
    prepared: list[tuple[ClientDebtor, str, str]] = []
    for debtor, kind in jobs:
        bal = debtor.balance()
        if bal <= 0 or not debtor.due_date:
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

    return _run(send_jobs_by_phone_async(prepared), timeout=3600)
