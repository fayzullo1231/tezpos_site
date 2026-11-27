"""
Qarz eslatmalari — Telethon (user session) orqali Telegramga yuborish.

Raqam bo‘yicha topiladi, kontakt saqlanadi va xabar shu chatga yoziladi.
"""
from __future__ import annotations

import asyncio
import hashlib
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
KIND_BALANCE = "balance"

PLACEHOLDER_NAMES = {"tezpos", "qarz", "mijoz", "tp"}

# Telegram: resolvePhone — max 1 so‘rov ~3 soniyada
RESOLVE_PAUSE_SEC = 3.5
SEND_PAUSE_SEC = 1.5

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _utf16_len(s: str) -> int:
    return len((s or "").encode("utf-16-le")) // 2


def _clean_http_url(raw: str) -> str:
    url = (raw or "").rstrip(".,);]")
    return "".join(ch for ch in url if ord(ch) < 128)


def prepare_telegram_dm(text: str) -> tuple[str, list]:
    """
    Userbot HTML <a> ni ko‘k qilmaydi. Chek URL ni alohida qatorda qoldiramiz
    va MessageEntityUrl qo‘yamiz — Telegram o‘zi bosiladigan qiladi.
    """
    from telethon.tl.types import MessageEntityBold, MessageEntityUrl

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\u200b", "").replace("\u200c", "").replace("\ufeff", "")
    url = ""
    m = _URL_RE.search(raw)
    if m:
        url = _clean_http_url(m.group(0))
        before = raw[: m.start()]
        after = raw[m.end() :]
        before = re.sub(r"(?im)^Chek\s*:\s*$", "Chek:", before)
        before = re.sub(r"(?im)Chek\s*:\s*$", "Chek:", before)
        raw = (before.rstrip() + "\n\n" + url + after).strip() + "\n"

    entities: list = []
    first = raw.split("\n", 1)[0]
    if first.strip():
        entities.append(MessageEntityBold(offset=0, length=_utf16_len(first)))
    if url and url in raw:
        off = _utf16_len(raw[: raw.rfind(url)])
        entities.append(MessageEntityUrl(offset=off, length=_utf16_len(url)))
    return raw, entities


def format_text_for_telegram(text: str) -> str:
    body, _ents = prepare_telegram_dm(text)
    return body


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


def _shop_template_label(shop_key: str) -> str:
    tpl = DebtSmsTemplate.objects.filter(shop_key=shop_key).first()
    label = ((tpl.shop_label if tpl else "") or "").strip()
    if is_usmon_family(shop_key) and label.lower() in {
        "",
        "tezpos",
        "product",
        "produkt",
        "продукт",
        "import",
        "usmon",
    }:
        return "Import Product"
    return label or "Kulol Optom"


def build_reminder_text(
    *,
    kind: str,
    shop: str,
    amount,
    due: date | None,
    overdue_days: int = 0,
) -> str:
    del kind, due, overdue_days
    head = (shop or "Import Product").strip() or "Import Product"
    money = devsms._fmt_som(amount)
    return f"{head}\n\nQarz: {money} so'm\n\nQoldiq: {money} so'm"


def parse_remind_weekdays(raw) -> set[int]:
    """'0,1,2,3,4,5,6' → {0..6}. Bo‘sh yoki yaroqsiz → barcha kunlar."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return set(range(7))
    out: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        try:
            d = int(part)
        except (TypeError, ValueError):
            continue
        if 0 <= d <= 6:
            out.add(d)
    return out or set(range(7))


def reminder_settings_for_shop(shop_key: str) -> dict[str, Any]:
    shop = (shop_key or "").strip().lower()
    row = DebtSmsTemplate.objects.filter(shop_key=shop).first() if shop else None
    weekdays = parse_remind_weekdays(
        getattr(row, "remind_weekdays", None) if row else "0,1,2,3,4,5,6"
    )
    overdue = 1
    if row is not None:
        try:
            overdue = max(0, int(row.remind_overdue_days))
        except (TypeError, ValueError):
            overdue = 1
    return {
        "weekdays": weekdays,
        "overdue_days": overdue,
        "remind_soon": True if row is None else bool(row.remind_soon),
        "remind_due_today": True if row is None else bool(row.remind_due_today),
    }


def reminder_kind_for(
    due: date | None,
    today: date | None = None,
    *,
    overdue_days: int = 1,
    remind_soon: bool = True,
    remind_due_today: bool = True,
) -> str | None:
    if not due:
        return None
    today = today or timezone.localdate()
    delta = (due - today).days
    if delta == 1 and remind_soon:
        return KIND_SOON
    if delta == 0 and remind_due_today:
        return KIND_DUE_TODAY
    if delta < 0:
        late = -delta
        need = max(0, int(overdue_days or 0))
        if need <= 0 or late >= need:
            return KIND_OVERDUE
    return None


SHARED_TELETHON_SHOPS = frozenset({"kuloloptom", "kuloloptom-2"})


USMON_FAMILY_KEYS = frozenset({"usmon", "import", "importproduct", "importoptom"})


def is_usmon_family(shop: str | None) -> bool:
    """Usmon / import / Importproduct — bitta Komron userboti."""
    s = (shop or "").strip().lower()
    if not s:
        return False
    if s in USMON_FAMILY_KEYS:
        return True
    if "usmon" in s or s.startswith("import"):
        return True
    return False


def uses_shared_telethon(shop_key: str | None) -> bool:
    return (shop_key or "").strip().lower() in SHARED_TELETHON_SHOPS


def related_debt_shops(shop: str | None) -> list[str]:
    """Guruh havolasi faqat shu do‘konniki. Boshqa serverga ulashilmaydi."""
    s = (shop or "").strip().lower()
    return [s] if s else []


def _global_creds() -> tuple[str, str, str]:
    return (
        str(getattr(settings, "TELETHON_API_ID", "") or "").strip(),
        str(getattr(settings, "TELETHON_API_HASH", "") or "").strip(),
        str(getattr(settings, "TELETHON_SESSION", "") or "").strip(),
    )


def _clean_cred(v: str) -> str:
    return (v or "").strip().strip('"').strip("'")


def _creds_from_tpl(row) -> tuple[str, str, str]:
    """Do‘kon sessioni. API juftligi to‘liq bo‘lmasa — .env juftligi."""
    g_id, g_hash, _g_sess = _global_creds()
    if not row:
        return ("", "", "")
    own_id = _clean_cred(row.telethon_api_id)
    own_hash = _clean_cred(row.telethon_api_hash)
    session = _clean_cred(row.telethon_session)
    if own_id and own_hash:
        return (own_id, own_hash, session)
    if session and g_id and g_hash and not own_id and not own_hash:
        return (g_id, g_hash, session)
    return (own_id, own_hash, session)


def _template_for_shop(shop: str):
    shop = (shop or "").strip()
    if not shop:
        return None
    return (
        DebtSmsTemplate.objects.filter(shop_key__iexact=shop)
        .only(
            "shop_key",
            "telethon_api_id",
            "telethon_api_hash",
            "telethon_session",
            "telethon_user",
        )
        .first()
    )


def _kulol_shared_creds() -> tuple[str, str, str]:
    """Kulol Optom userboti: avval saytdagi session, bo‘lmasa server .env."""
    tpl = _template_for_shop("kuloloptom")
    creds = _creds_from_tpl(tpl)
    if _creds_ok(*creds):
        return creds
    g = _global_creds()
    if _creds_ok(*g):
        return g
    return creds if any(creds) else ("", "", "")


def telethon_creds(shop_key: str | None = None) -> tuple[str, str, str]:
    """kuloloptom va kuloloptom-2 — bitta Kulol Optom userboti. Qolganlar o‘z sessioni."""
    shop = (shop_key or "").strip().lower()
    if not shop:
        return _global_creds()
    if shop in SHARED_TELETHON_SHOPS:
        shared = _kulol_shared_creds()
        if _creds_ok(*shared):
            return shared

    tpl = _template_for_shop(shop)
    creds = _creds_from_tpl(tpl)
    if _creds_ok(*creds):
        return creds
    if is_usmon_family(shop):
        for row in DebtSmsTemplate.objects.filter(shop_key__istartswith="usmon").only(
            "telethon_api_id",
            "telethon_api_hash",
            "telethon_session",
            "telethon_user",
        ):
            other = _creds_from_tpl(row)
            if _creds_ok(*other):
                return other
        for row in DebtSmsTemplate.objects.exclude(telethon_session="").only(
            "shop_key",
            "telethon_api_id",
            "telethon_api_hash",
            "telethon_session",
            "telethon_user",
        ):
            if not is_usmon_family(row.shop_key):
                continue
            user = (row.telethon_user or "").strip().lower()
            if "komron" in user or "kamron" in user:
                other = _creds_from_tpl(row)
                if _creds_ok(*other):
                    return other
    return creds if any(creds) else ("", "", "")


def _creds_ok(api_id: str, api_hash: str, session: str) -> bool:
    return bool(api_id and api_hash and session and str(api_id).isdigit())


def telethon_configured(shop_key: str | None = None) -> bool:
    api_id, api_hash, session = telethon_creds(shop_key)
    return _creds_ok(api_id, api_hash, session)


def _mask_secret(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "••••"
    return s[:4] + "…" + s[-4:]


def telethon_public_status(shop_key: str | None = None) -> dict:
    shop = (shop_key or "").strip().lower()
    api_id, api_hash, session = telethon_creds(shop)
    configured = _creds_ok(api_id, api_hash, session)
    tpl = _template_for_shop(shop)
    user = (tpl.telethon_user or "").strip() if tpl else ""
    own_ok = _creds_ok(*_creds_from_tpl(tpl))
    if shop == "kuloloptom-2" and configured:
        src = _template_for_shop("kuloloptom")
        src_user = (src.telethon_user or "").strip() if src else ""
        user = src_user or user or "Kulol Optom"
    elif not user and shop == "kuloloptom" and configured and not own_ok:
        user = "Server akkaunti"
    return {
        "shared": False,
        "configured": configured,
        "user": user,
        "api_id": api_id if configured else "",
        "api_hash_masked": _mask_secret(api_hash) if configured else "",
        "session_set": bool(session),
    }


def _add_group(groups: dict, api_id: str, api_hash: str, session: str, shop: str) -> None:
    if not _creds_ok(api_id, api_hash, session):
        return
    shop = (shop or "").strip().lower()
    if not shop:
        return
    fp = hashlib.sha256(f"{api_id}:{session}".encode("utf-8")).hexdigest()
    row = groups.setdefault(
        fp,
        {"api_id": api_id, "api_hash": api_hash, "session": session, "shops": []},
    )
    if shop not in row["shops"]:
        row["shops"].append(shop)


def session_groups() -> list[dict]:
    """kuloloptom-2 Kulol Optom userbotida. Import/Usmon o‘z Komronida. Qolganlar alohida."""
    groups: dict[str, dict] = {}
    own_shops: set[str] = set()

    for row in DebtSmsTemplate.objects.all():
        shop = (row.shop_key or "").strip().lower()
        if not shop:
            continue
        if shop == "kuloloptom-2" and _creds_ok(*_kulol_shared_creds()):
            continue
        api_id, api_hash, session = _creds_from_tpl(row)
        user = (row.telethon_user or "").lower()
        if not _creds_ok(api_id, api_hash, session):
            msg = (
                f"session_groups skip shop={shop} user={row.telethon_user or '-'} "
                f"api_id={'ha' if api_id else 'yoq'} "
                f"hash={'ha' if api_hash else 'yoq'} "
                f"session={'ha' if session else 'yoq'}"
            )
            logger.warning(msg)
            print(msg, flush=True)
            continue
        _add_group(groups, api_id, api_hash, session, shop)
        own_shops.add(shop)
        if is_usmon_family(shop) and (
            "komron" in user or "kamron" in user or is_usmon_family(shop)
        ):
            _add_group(groups, api_id, api_hash, session, "usmon")
            _add_group(groups, api_id, api_hash, session, "import")
            _add_group(groups, api_id, api_hash, session, "importproduct")
            _add_group(groups, api_id, api_hash, session, "importoptom")
    usmon_fp = None
    for fp, row in groups.items():
        if "usmon" in (row.get("shops") or []):
            usmon_fp = fp
            break
    if usmon_fp:
        extra = set(USMON_FAMILY_KEYS)
        for t in DebtSmsTemplate.objects.all().only("shop_key"):
            k = (t.shop_key or "").strip().lower()
            if k and is_usmon_family(k):
                extra.add(k)
        row = groups[usmon_fp]
        for k in extra:
            if k not in row["shops"]:
                row["shops"].append(k)
    if "kuloloptom" not in own_shops:
        g_id, g_hash, g_sess = _global_creds()
        if _creds_ok(g_id, g_hash, g_sess):
            _add_group(groups, g_id, g_hash, g_sess, "kuloloptom")
    for row in groups.values():
        shops = row.get("shops") or []
        if "kuloloptom" in shops and "kuloloptom-2" not in shops:
            shops.append("kuloloptom-2")
    return list(groups.values())


def describe_sessions() -> list[str]:
    """Serverda qaysi userbot ochilishini ko‘rsatadi (sir emas)."""
    lines: list[str] = []
    g_id, g_hash, g_sess = _global_creds()
    lines.append(
        "env TELETHON: api_id="
        + ("ha" if g_id else "yoq")
        + " hash="
        + ("ha" if g_hash else "yoq")
        + " session="
        + ("ha" if g_sess else "yoq")
    )
    for row in DebtSmsTemplate.objects.order_by("shop_key"):
        api_id, api_hash, session = _creds_from_tpl(row)
        lines.append(
            f"tpl shop={row.shop_key} user={row.telethon_user or '-'} "
            f"own_api={_clean_cred(row.telethon_api_id) or '-'} "
            f"own_hash={'ha' if _clean_cred(row.telethon_api_hash) else 'yoq'} "
            f"own_session={'ha' if _clean_cred(row.telethon_session) else 'yoq'} "
            f"ok={_creds_ok(api_id, api_hash, session)}"
        )
    for g in session_groups():
        lines.append("GROUP shops=" + ",".join(sorted(g.get("shops") or [])))
    if not session_groups():
        lines.append("GROUP yo‘q")
    return lines


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


async def _client_from_creds(api_id: str, api_hash: str, session: str):
    from telethon import TelegramClient
    from telethon.errors import AuthKeyDuplicatedError, AuthKeyUnregisteredError
    from telethon.sessions import StringSession

    if not _creds_ok(str(api_id), str(api_hash), str(session)):
        raise RuntimeError("Telethon sozlanmagan")
    client = TelegramClient(
        StringSession(str(session).strip()),
        int(str(api_id).strip()),
        str(api_hash).strip(),
    )
    try:
        await client.connect()
    except (AuthKeyDuplicatedError, AuthKeyUnregisteredError) as exc:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise RuntimeError(
            "String session o‘chgan (ikki joyda ochilgan). "
            "Komron uchun YANGI session yarating va saytga saqlang."
        ) from exc
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session avtorizatsiyadan o‘tmagan")
    return client


async def _client(shop_key: str | None = None):
    api_id, api_hash, session = await sync_to_async(telethon_creds)(shop_key)
    return await _client_from_creds(api_id, api_hash, session)


def verify_telethon_session(api_id: str, api_hash: str, session: str) -> dict[str, Any]:
    """String session + API bilan kirishni tekshiradi."""

    async def _go():
        client = await _client_from_creds(api_id, api_hash, session)
        try:
            me = await client.get_me()
            username = (getattr(me, "username", None) or "") or ""
            first = (getattr(me, "first_name", None) or "") or ""
            last = (getattr(me, "last_name", None) or "") or ""
            name = (first + " " + last).strip()
            display = ("@" + username) if username else (name or str(getattr(me, "id", "") or ""))
            return {
                "ok": True,
                "user": display,
                "user_id": str(getattr(me, "id", "") or ""),
            }
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    try:
        return _run(_go(), timeout=40)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _uz_phone_digits(phone_n: str) -> str:
    """Faqat 998XXXXXXXXX (12). Qisqa 998386922 kabi → bo‘sh (Telegram xato bermasin)."""
    from .devsms import normalize_phone

    return normalize_phone(phone_n)


def _intl_phone(phone_n: str) -> str:
    d = _uz_phone_digits(phone_n)
    return ("+" + d) if d else ""


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
    access = getattr(u, "access_hash", None)
    return {
        "ok": True,
        "status": "ok",
        "telegram_id": str(u.id),
        "access_hash": "" if access in (None, 0) else str(access),
        "username": username,
        "display_name": display,
        "error": "",
        "user": u,
    }


def _meta_no_tg(err: str = "Telegram topilmadi") -> dict:
    return {
        "ok": False,
        "status": "no_telegram",
        "telegram_id": "",
        "username": "",
        "display_name": "",
        "error": "Telegram topilmadi"
        if err in ("", "resolve_privacy", "resolve_empty", "import_empty", "popular")
        else (err or "Telegram topilmadi"),
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


def _phone_not_occupied(exc) -> bool:
    name = type(exc).__name__.upper()
    msg = (getattr(exc, "message", None) or str(exc) or "").upper()
    return (
        "NOT_OCCUPIED" in name
        or "NOT_OCCUPIED" in msg
        or "NOT YET BEING USED" in msg
    )


def _phone_variants(phone_n: str) -> list[str]:
    """Mahalliy 9 xona bo‘lsa ham 998XXXXXXXXX va +998XXXXXXXXX qidiriladi."""
    digits = _uz_phone_digits(phone_n)
    if not digits:
        return []
    # Avval +998 (Telegram ko‘pincha shunday), keyin 998...
    out = ["+" + digits, digits]
    return out


async def _resolve_phone(client, phone_n: str) -> dict:
    """
    contacts.resolvePhone — raqam orqali topish.
    Avval 998XXXXXXXXX, bo‘lmasa +998XXXXXXXXX.
    """
    from telethon.errors import FloodWaitError, RPCError
    from telethon.tl.functions.contacts import ResolvePhoneRequest

    variants = _phone_variants(phone_n)
    if not variants:
        return _meta_err("Telefon yo‘q")

    floods = 0
    saw_not_occupied = False
    saw_empty = False
    last_err = ""
    for idx, phone in enumerate(variants):
        if idx:
            await asyncio.sleep(RESOLVE_PAUSE_SEC)
        while True:
            try:
                result = await client(ResolvePhoneRequest(phone=phone))
                users = list(getattr(result, "users", None) or [])
                if not users:
                    peer = getattr(result, "peer", None)
                    uid = getattr(peer, "user_id", None) if peer is not None else None
                    if uid:
                        try:
                            user = await client.get_entity(int(uid))
                            return _meta_ok(user)
                        except Exception:
                            pass
                    # Bo‘sh javob — privacy bo‘lishi mumkin, import hali topishi mumkin
                    saw_empty = True
                    break
                return _meta_ok(users[0])
            except FloodWaitError as exc:
                floods += 1
                if floods > 2:
                    return _meta_err("FloodWait")
                wait = min(int(getattr(exc, "seconds", 0) or 0) + 1, 15)
                logger.warning("FloodWait %ss — kutilyapti", wait)
                await asyncio.sleep(wait)
            except RPCError as exc:
                if _phone_not_occupied(exc):
                    saw_not_occupied = True
                    break
                logger.warning("resolvePhone RPC %s", exc)
                last_err = str(exc)[:200]
                break
            except Exception as exc:
                logger.warning("resolvePhone xato %s", exc)
                last_err = str(exc)[:200]
                break
    # Faqat aniq NOT_OCCUPIED → topilmadi. Qolganlari import qilinadi.
    if saw_not_occupied and not last_err:
        return _meta_no_tg("resolve_empty")
    if last_err and "not_occupied" in last_err.lower():
        return _meta_no_tg(last_err)
    if saw_empty:
        return _meta_err("resolve_privacy")
    return _meta_err(last_err or "resolve_empty")


def _user_from_import(result):
    users = list(getattr(result, "users", None) or [])
    imported = list(getattr(result, "imported", None) or [])
    if imported and users:
        uid = int(getattr(imported[0], "user_id", 0) or 0)
        return next((u for u in users if int(u.id) == uid), users[0])
    if users:
        return users[0]
    return None


def _parse_import(result, by_cid: dict):
    users = {}
    for u in list(getattr(result, "users", None) or []):
        try:
            users[int(u.id)] = u
        except (TypeError, ValueError):
            continue
    found = []
    for imp in list(getattr(result, "imported", None) or []):
        try:
            cid = int(getattr(imp, "client_id", 0) or 0)
            uid = int(getattr(imp, "user_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        user = users.get(uid)
        if user is None or cid not in by_cid:
            continue
        found.append((cid, user))
    if not found and len(users) == 1 and len(by_cid) == 1:
        found.append((next(iter(by_cid)), next(iter(users.values()))))
    retry = []
    for item in list(getattr(result, "retry_contacts", None) or []):
        try:
            retry.append(int(item))
        except (TypeError, ValueError):
            continue
    popular = []
    for item in list(getattr(result, "popular_invites", None) or []):
        try:
            popular.append(int(getattr(item, "client_id", item) or 0))
        except (TypeError, ValueError):
            continue
    return found, retry, popular


async def _import_user_by_phone(client, phone_n: str, contact_name: str = "") -> dict:
    """
    Bitta raqamni kontakt qilib oladi.
    popular_invites «Telegram yo‘q» emas — ko‘p odam saqlagan raqam.
    Kontakt saqlanadi, aks holda xabar chatga tushmaydi.
    """
    from telethon.errors import FloodWaitError, RPCError
    from telethon.tl.functions.contacts import AddContactRequest, ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    phones = _phone_variants(phone_n)
    if not phones:
        return _meta_err("Telefon yo‘q")
    first = (contact_name or "").strip()[:32] or "Mijoz"
    saw_popular = False
    saw_empty = False
    last = _meta_err("Telegram vaqtincha topilmadi")

    for idx, phone in enumerate(phones):
        if idx:
            await asyncio.sleep(RESOLVE_PAUSE_SEC)
        client_id = random.randint(1, 0x7FFFFFFF)
        for attempt in range(2):
            contact = InputPhoneContact(
                client_id=client_id,
                phone=phone,
                first_name=first,
                last_name="",
            )
            try:
                result = await client(ImportContactsRequest([contact]))
            except FloodWaitError as exc:
                wait = min(int(getattr(exc, "seconds", 0) or 0) + 1, 12)
                logger.warning("FloodWait import %ss", wait)
                await asyncio.sleep(wait)
                continue
            except RPCError as exc:
                logger.warning("import RPC %s", exc)
                last = _meta_err(str(exc))
                break
            user = _user_from_import(result)
            if user is not None:
                meta = _meta_ok(user)
                meta["_imported"] = True
                try:
                    await client(
                        AddContactRequest(
                            id=user,
                            first_name=first,
                            last_name="",
                            phone=phone,
                            add_phone_privacy_exception=True,
                        )
                    )
                except Exception:
                    logger.warning("addContact xato", exc_info=True)
                return meta
            _found, retry, popular = _parse_import(result, {client_id: phone})
            if retry and attempt == 0:
                await asyncio.sleep(2)
                continue
            if popular:
                saw_popular = True
            else:
                saw_empty = True
            break
    if saw_popular:
        return _meta_err("popular")
    if saw_empty:
        return _meta_err("import_empty")
    return last


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


async def _resolve_phone_for_send(client, phone_n: str, contact_name: str = "") -> dict:
    """
    +998 resolvePhone → importContacts.
    popular_invites = Telegram BOR, lekin yopiq — «topilmadi» deb yozilmaydi.
    """
    phone_n = _uz_phone_digits(phone_n) or phone_n
    resolved = await _resolve_phone(client, phone_n)
    if resolved.get("ok") and resolved.get("user") is not None:
        return resolved
    imported = await _import_user_by_phone(client, phone_n, contact_name)
    if imported.get("ok") and imported.get("user") is not None:
        return imported
    # popular = raqamda Telegram bor (privacy) — qayta uriniladi
    if imported.get("error") == "popular":
        return _meta_err("popular")
    # Aniq yo‘q: NOT_OCCUPIED + import bo‘sh
    if (
        resolved.get("status") == "no_telegram"
        and imported.get("error") == "import_empty"
    ):
        return _meta_no_tg()
    if resolved.get("status") == "no_telegram" and imported.get("status") == "error":
        # Import flood/RPC — hali topilmadi deb mahkamlamaymiz
        return _meta_err(imported.get("error") or "import_retry")
    if imported.get("error") == "import_empty":
        # Resolve privacy bo‘lishi mumkin — qayta urinish
        return _meta_err("pending")
    return imported if isinstance(imported, dict) else _meta_err("Telegram topilmadi")


def debtors_pending_telegram(shops, limit: int = 120) -> list:
    """Hali topilmagan, telefoni bor qarzdorlar. Har userbot faqat o‘z do‘koni."""
    from django.db.models import F

    keys = sorted({str(s or "").strip().lower() for s in (shops or []) if str(s or "").strip()})
    if not keys:
        return []
    return list(
        ClientDebtor.objects.filter(shop_key__in=keys, is_active=True)
        .exclude(phone="")
        .exclude(telegram_status="ok")
        .order_by(F("telegram_checked_at").asc(nulls_first=True), "id")[: max(1, limit)]
    )


async def resolve_pending_on_client(client, debtors) -> int:
    """
    Telefon daftari kabi bitta importContacts.
    popular_invites «Telegram yo‘q» qilib yozilmaydi.
    Topilmaganlar sekin resolvePhone bilan qayta tekshiriladi.
    """
    from telethon.errors import FloodWaitError, RPCError
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    rows = []
    for row in debtors or []:
        digits = _uz_phone_digits(getattr(row, "phone", "") or "")
        if not digits:
            continue
        rows.append((row, digits))
    if not rows:
        return 0

    found = 0
    missed: list[tuple] = []

    async def _save_found(row, user) -> None:
        nonlocal found
        meta = _meta_ok(user)
        meta["_imported"] = True
        clean = {k: v for k, v in meta.items() if k not in ("user", "_privacy", "_imported")}
        await apply_telegram_meta(row, clean)
        found += 1
        print(f"telegram topildi {row.name}", flush=True)

    async def _import_chunk(chunk, plus: bool):
        by_cid = {}
        contacts = []
        for row, digits in chunk:
            cid = random.randint(1, 0x7FFFFFFF)
            while cid in by_cid:
                cid = random.randint(1, 0x7FFFFFFF)
            phone = ("+" + digits) if plus else digits
            by_cid[cid] = (row, digits)
            contacts.append(
                InputPhoneContact(
                    client_id=cid,
                    phone=phone,
                    first_name=(getattr(row, "name", "") or "Mijoz").strip()[:32] or "Mijoz",
                    last_name="",
                )
            )
        result = await client(ImportContactsRequest(contacts))
        got, retry, popular = _parse_import(result, by_cid)
        print(
            f"telegram import plus={int(plus)} soni={len(chunk)} "
            f"topildi={len(got)} popular={len(popular)} retry={len(retry)}",
            flush=True,
        )
        hit = set()
        for cid, user in got:
            pair = by_cid.get(cid)
            if pair is None:
                continue
            await _save_found(pair[0], user)
            hit.add(cid)
        if retry:
            await asyncio.sleep(RESOLVE_PAUSE_SEC)
            again = [c for c in contacts if c.client_id in set(retry) and c.client_id not in hit]
            if again:
                result2 = await client(ImportContactsRequest(again))
                got2, _retry2, _pop2 = _parse_import(result2, by_cid)
                for cid, user in got2:
                    if cid in hit:
                        continue
                    pair = by_cid.get(cid)
                    if pair is None:
                        continue
                    await _save_found(pair[0], user)
                    hit.add(cid)
        left = [by_cid[cid] for cid in by_cid if cid not in hit]
        return left

    use_plus = False
    try:
        for start in range(0, len(rows), 40):
            if not client.is_connected():
                break
            chunk = rows[start : start + 40]
            left = await _import_chunk(chunk, use_plus)
            if start == 0 and not use_plus and len(left) == len(chunk):
                await asyncio.sleep(RESOLVE_PAUSE_SEC)
                left = await _import_chunk(chunk, True)
                if len(left) < len(chunk):
                    use_plus = True
                else:
                    missed.extend(left)
                    print("telegram import bo'sh — limit yoki raqam yopiq", flush=True)
                    break
            missed.extend(left)
            await asyncio.sleep(RESOLVE_PAUSE_SEC)
    except FloodWaitError as exc:
        print(f"telegram qidiruv to'xtadi: flood {getattr(exc, 'seconds', '')}", flush=True)
        return found
    except RPCError as exc:
        print(f"telegram import xato: {exc}", flush=True)
        return found

    streak = 0
    for row, digits in missed:
        if not client.is_connected():
            break
        try:
            meta2 = await _resolve_phone_for_send(
                client, digits, str(getattr(row, "name", "") or "")
            )
        except Exception as exc:
            logger.warning("resolve_for_send qoldiq %s", exc)
            break
        if meta2.get("ok") and meta2.get("user") is not None:
            await _save_found(row, meta2["user"])
            streak = 0
        elif meta2.get("status") == "no_telegram" and found > 0:
            clean = {
                k: v
                for k, v in meta2.items()
                if k not in ("user", "_privacy", "_imported")
            }
            await apply_telegram_meta(row, clean)
            streak += 1
        elif "flood" in str(meta2.get("error") or "").lower():
            print("telegram qidiruv to'xtadi: flood", flush=True)
            break
        else:
            # popular/pending — status error, qayta uriniladi
            clean = {
                k: v
                for k, v in meta2.items()
                if k not in ("user", "_privacy", "_imported")
            }
            if clean:
                await apply_telegram_meta(row, clean)
            streak += 1
            if found == 0 and streak >= 5:
                print("telegram qidiruv to'xtadi: limit", flush=True)
                break
        await asyncio.sleep(RESOLVE_PAUSE_SEC)
    return found


@sync_to_async
def _save_status(debtor: ClientDebtor, status: str) -> None:
    debtor.telegram_status = status
    debtor.save(update_fields=["telegram_status", "updated_at"])


@sync_to_async
def apply_telegram_meta(debtor: ClientDebtor, meta: dict) -> None:
    """Topilgan id saqlanadi. popular/privacy «topilmadi» qilib mahkamlanmaydi."""
    status = meta.get("status") or "error"
    new_id = str(meta.get("telegram_id") or "").strip()
    new_hash = str(meta.get("access_hash") or "").strip()
    new_user = str(meta.get("username") or "").strip()
    new_name = str(meta.get("display_name") or "").strip()
    if status == "ok" and new_id:
        debtor.telegram_status = "ok"
        debtor.telegram_id = new_id
        if new_hash:
            debtor.telegram_access_hash = new_hash[:32]
        if new_user:
            debtor.telegram_username = new_user
        if new_name:
            debtor.telegram_name = new_name
    elif status == "no_telegram" and not (debtor.telegram_id or "").strip():
        debtor.telegram_status = "no_telegram"
        debtor.telegram_id = ""
        debtor.telegram_access_hash = ""
        debtor.telegram_username = ""
        debtor.telegram_name = ""
    elif status == "no_phone" and not (debtor.telegram_id or "").strip():
        debtor.telegram_status = "no_phone"
    elif not (debtor.telegram_id or "").strip():
        # popular / privacy / flood — qayta tekshiriladi, topilmadi emas
        debtor.telegram_status = "error"
    debtor.telegram_checked_at = timezone.now()
    debtor.save(
        update_fields=[
            "telegram_status",
            "telegram_id",
            "telegram_access_hash",
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

    by_shop: dict[str, list[ClientDebtor]] = {}
    for d in need:
        by_shop.setdefault((d.shop_key or "").strip().lower(), []).append(d)

    # Yangi Telethon client ochilmaydi (AuthKeyDuplicated).
    return debtors


def _peer_from_hash(telegram_id: str, access_hash: str):
    from telethon.tl.types import InputPeerUser

    tid = str(telegram_id or "").strip()
    raw = str(access_hash or "").strip()
    if not tid.isdigit() or not raw.lstrip("-").isdigit():
        return None
    try:
        hashed = int(raw)
    except (TypeError, ValueError):
        return None
    if hashed == 0:
        return None
    return InputPeerUser(int(tid), hashed)


async def _send_by_phone(
    client,
    phone_n: str,
    text: str,
    *,
    prefer_telegram_id: str = "",
    access_hash: str = "",
    username: str = "",
    contact_name: str = "",
) -> dict:
    """
    DevSMS bilan bir xil matnni Telegramga yuboradi.
    Tartib: saqlangan peer → username → raqam. Topilgan kontakt saqlanadi.
    """
    from telethon.errors import FloodWaitError

    user = None
    meta: dict = {}
    imported_temp = False

    tid = str(prefer_telegram_id or "").strip()
    direct_peer = _peer_from_hash(tid, access_hash)
    if direct_peer is not None:
        try:
            user = await client.get_entity(direct_peer)
            meta = _meta_ok(user)
        except Exception:
            user = None
            meta = {
                "ok": True,
                "status": "ok",
                "telegram_id": tid,
                "access_hash": str(access_hash).strip(),
                "username": str(username or "").strip().lstrip("@"),
                "display_name": ("@" + str(username).strip().lstrip("@"))
                if str(username or "").strip()
                else "Telegram bor",
                "error": "",
            }
    if user is None and direct_peer is None and tid.isdigit():
        try:
            entity = await client.get_entity(int(tid))
            user = entity
            meta = _meta_ok(entity)
        except Exception:
            user = None

    uname = str(username or "").strip().lstrip("@")
    if user is None and direct_peer is None and uname:
        try:
            entity = await client.get_entity(uname)
            user = entity
            meta = _meta_ok(entity)
        except Exception:
            user = None

    if user is None and direct_peer is None:
        meta = await _resolve_phone_for_send(client, phone_n, contact_name)
        user = meta.get("user")
        imported_temp = bool(meta.get("_imported"))

    clean_meta = {
        k: v for k, v in meta.items() if k not in ("user", "_privacy", "_imported")
    }
    target = user if user is not None else direct_peer
    if not meta.get("ok") or target is None:
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
            body, ents = prepare_telegram_dm(text)
            try:
                await client.send_message(
                    target,
                    body,
                    formatting_entities=ents or None,
                    link_preview=True,
                )
            except FloodWaitError:
                raise
            except Exception:
                await client.send_message(target, body, link_preview=True)
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

    if not send_ok and not imported_temp and phone_n:
        meta2 = await _resolve_phone_for_send(client, phone_n, contact_name)
        user2 = meta2.get("user")
        if meta2.get("ok") and user2 is not None:
            user = user2
            imported_temp = bool(meta2.get("_imported"))
            clean_meta = {
                k: v
                for k, v in meta2.items()
                if k not in ("user", "_privacy", "_imported")
            }
            try:
                body, ents = prepare_telegram_dm(text)
                try:
                    await client.send_message(
                        user2,
                        body,
                        formatting_entities=ents or None,
                        link_preview=True,
                    )
                except Exception:
                    await client.send_message(user2, body, link_preview=True)
                send_ok = True
                send_err = ""
            except Exception as exc:
                send_err = str(exc)[:200]

    # Kontakt saqlanadi. O‘chirilsa xabar chatga tushmay, so‘rovlar ichida qoladi.
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
        "telegram_id": str(getattr(user, "id", "") or clean_meta.get("telegram_id") or ""),
        "meta": clean_meta,
    }


async def send_jobs_by_phone_async(
    jobs: list[tuple[ClientDebtor, str, str]],
) -> list[dict]:
    """jobs: [(debtor, kind, text), ...] — har biri raqam orqali."""
    if not jobs:
        return []
    by_shop: dict[str, list[tuple[ClientDebtor, str, str]]] = {}
    for job in jobs:
        by_shop.setdefault((job[0].shop_key or "").strip().lower(), []).append(job)

    out: list[dict] = []
    for shop, shop_jobs in by_shop.items():
        if not await sync_to_async(telethon_configured)(shop):
            for debtor, _kind, _text in shop_jobs:
                out.append(
                    {
                        "ok": False,
                        "error": "Telethon sozlanmagan",
                        "debtor_id": debtor.pk,
                        "name": debtor.name,
                    }
                )
            continue
        for debtor, kind, text in shop_jobs:
            res = await sync_to_async(enqueue_text_by_phone)(debtor, text)
            if res.get("ok"):
                await _mark_reminded(debtor, kind)
            out.append(
                {
                    "ok": bool(res.get("ok")),
                    "error": res.get("error") or "",
                    "queued": bool(res.get("queued")),
                    "debtor_id": debtor.pk,
                    "name": debtor.name,
                }
            )
    return out


def resolve_debtors_batch(
    debtors: Iterable[ClientDebtor], *, force: bool = False
) -> list[ClientDebtor]:
    rows = list(debtors)
    return _run(resolve_debtors_batch_async(rows, force=force), timeout=3600)


def send_text_by_phone(
    phone: str, text: str, *, debtor: ClientDebtor | None = None
) -> dict[str, Any]:
    """Listener orqali yuboradi — yangi Telethon client ochilmaydi."""
    shop_key = (getattr(debtor, "shop_key", "") or "") if debtor is not None else ""
    if not telethon_configured(shop_key or None):
        return {"ok": False, "error": "Telethon sozlanmagan"}
    phone_n = devsms.normalize_phone(phone)
    if not phone_n:
        return {"ok": False, "error": "Telefon yo‘q"}
    msg = (text or "").strip()
    if not msg:
        return {"ok": False, "error": "Matn bo‘sh"}
    if debtor is not None:
        return enqueue_text_by_phone(debtor, msg)
    from .tg_outbox import write_job
    from .tg_send_local import send_via_listener

    job = {
        "type": "dm",
        "shop": (shop_key or "").strip().lower(),
        "phone": phone_n,
        "telegram_id": "",
        "text": msg,
    }
    res = send_via_listener(job, timeout=40)
    if res.get("ok"):
        return res
    write_job(job)
    return {"ok": False, "queued": True, "error": res.get("error") or "navbatda"}


_GROUP_LINK_PRIVATE = re.compile(
    r"^(?:https?://)?(?:www\.)?t(?:elegram)?\.me/c/(\d+)(?:/(\d+))?(?:/(\d+))?/?(?:\?.*)?$",
    re.IGNORECASE,
)
_GROUP_LINK_PUBLIC = re.compile(
    r"^(?:https?://)?(?:www\.)?t(?:elegram)?\.me/([A-Za-z][A-Za-z0-9_]{3,})(?:/(\d+))?(?:/(\d+))?/?(?:\?.*)?$",
    re.IGNORECASE,
)
_RESERVED_TG_PATHS = {"c", "s", "joinchat", "share", "addstickers", "proxy", "socks", "iv"}


def parse_group_link(link: str) -> dict | None:
    """
    https://t.me/c/4446533637/3 → {"chat": 4446533637, "topic": 3, ...}
    https://t.me/guruh/5       → {"chat": "guruh", "topic": 5, ...}
    """
    s = (link or "").strip()
    m = _GROUP_LINK_PRIVATE.match(s)
    if m:
        cid, a, _b = m.groups()
        topic = int(a) if a else None
        norm = f"https://t.me/c/{cid}" + (f"/{topic}" if topic else "")
        return {"chat": int(cid), "topic": topic, "normalized": norm}
    m = _GROUP_LINK_PUBLIC.match(s)
    if m:
        username, a, _b = m.groups()
        if username.lower() in _RESERVED_TG_PATHS:
            return None
        topic = int(a) if a else None
        norm = f"https://t.me/{username}" + (f"/{topic}" if topic else "")
        return {"chat": username, "topic": topic, "normalized": norm}
    return None


async def _resolve_group_entity(client, info: dict):
    from telethon.tl.types import PeerChannel

    chat = info["chat"]
    if isinstance(chat, int):
        candidates: list = [PeerChannel(chat), int(f"-100{chat}"), chat]
    else:
        candidates = [chat]
    last: Exception | None = None
    for target in candidates:
        try:
            return await client.get_entity(target)
        except Exception as exc:
            last = exc
    try:
        await client.get_dialogs(limit=800)
    except Exception as exc:
        last = exc
    for target in candidates:
        try:
            return await client.get_entity(target)
        except Exception as exc:
            last = exc
    raise last or RuntimeError("Guruh topilmadi")


def _sent_topic_id(msg) -> int | None:
    rt = getattr(msg, "reply_to", None)
    if not rt:
        return None
    top = getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
    try:
        return int(top) if top else None
    except (TypeError, ValueError):
        return None


async def send_group_on_client(client, link: str, text: str) -> dict[str, Any]:
    info = parse_group_link(link)
    if not info:
        return {"ok": False, "error": "Telegram havola noto‘g‘ri"}
    try:
        entity = await _resolve_group_entity(client, info)
    except Exception as exc:
        logger.exception("guruh entity topilmadi")
        return {"ok": False, "error": f"Guruh topilmadi: {exc}"[:220]}
    topic = info.get("topic")
    base: dict[str, Any] = {"parse_mode": "html", "link_preview": False}

    async def _try_send(**extra):
        kw = {**base, **extra}
        try:
            return await client.send_message(entity, text, **kw)
        except TypeError:
            kw.pop("top_msg_id", None)
            return await client.send_message(entity, text, **kw)

    title = getattr(entity, "title", "") or ""
    if topic:
        tid = int(topic)
        variants: list[dict[str, Any]] = []
        try:
            from telethon.tl.types import InputReplyToMessage

            try:
                variants.append(
                    {
                        "reply_to": InputReplyToMessage(
                            reply_to_msg_id=tid, top_msg_id=tid
                        )
                    }
                )
            except TypeError:
                variants.append(
                    {"reply_to": InputReplyToMessage(reply_to_msg_id=tid)}
                )
        except Exception:
            pass
        variants.append({"reply_to": tid, "top_msg_id": tid})
        variants.append({"reply_to": tid})
        last_err = "Mavzuga yozilmadi"
        for extra in variants:
            try:
                msg = await _try_send(**extra)
            except Exception as exc:
                last_err = str(exc)[:200]
                logger.warning(
                    "guruh topic send fail title=%s topic=%s err=%s",
                    title,
                    tid,
                    exc,
                )
                continue
            landed = _sent_topic_id(msg)
            logger.info(
                "guruh yuborildi title=%s want_topic=%s landed=%s",
                title,
                tid,
                landed,
            )
            return {
                "ok": True,
                "message_id": getattr(msg, "id", None),
                "title": title,
                "topic": tid,
                "landed_topic": landed,
            }
        return {
            "ok": False,
            "error": f"Mavzu {tid} ga yozilmadi: {last_err}",
        }

    try:
        msg = await _try_send()
    except Exception as exc:
        logger.warning("guruh send fail title=%s err=%s", title, exc)
        return {"ok": False, "error": str(exc)[:200]}
    return {
        "ok": True,
        "message_id": getattr(msg, "id", None),
        "title": title,
        "topic": None,
    }


async def _send_group_async(link: str, text: str, shop_key: str = "") -> dict[str, Any]:
    client = await _client(shop_key or None)
    try:
        return await send_group_on_client(client, link, text)
    except Exception as exc:
        logger.exception("send_group_async failed shop=%s", shop_key)
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def send_group_message(
    link: str, text: str, *, timeout: int = 60, shop_key: str = ""
) -> dict[str, Any]:
    """Gunicorn kutmasin — faqat navbat. Listener o‘zi yuboradi."""
    if not telethon_configured(shop_key or None):
        return {"ok": False, "error": "Telethon sozlanmagan"}
    if not (text or "").strip():
        return {"ok": False, "error": "Matn bo‘sh"}
    if not parse_group_link(link):
        return {"ok": False, "error": "Telegram havola noto‘g‘ri"}
    enqueue_group_message(link, text, shop_key)
    return {"ok": True, "queued": True}


def enqueue_group_message(link: str, text: str, shop_key: str = "") -> None:
    """Listener jarayoniga yozadi — gunicorn sessionni ikkinchi marta ochmasin."""
    from .tg_outbox import write_job

    write_job(
        {
            "type": "group",
            "shop": (shop_key or "").strip().lower(),
            "link": link,
            "text": text,
        }
    )


def enqueue_text_by_phone(debtor: ClientDebtor, text: str) -> dict:
    """Darhol navbatga yozadi — HTTP so‘rov Telethon/resolve kutmaydi."""
    if debtor is None or not (text or "").strip():
        return {"ok": False, "error": "Matn yoki mijoz yo‘q"}
    shop = (getattr(debtor, "shop_key", "") or "").strip().lower()
    if is_usmon_family(shop):
        shop = "usmon"
    phone_n = _uz_phone_digits(getattr(debtor, "phone", "") or "")
    if not phone_n:
        return {
            "ok": False,
            "error": "Telefon noto‘g‘ri — 9 xona yoki 998XXXXXXXXX yozing",
        }
    job = {
        "type": "dm",
        "shop": shop,
        "phone": phone_n,  # doim 998XXXXXXXXX — +998 bilan qidiriladi
        "debtor_id": int(debtor.pk),
        "telegram_id": str(getattr(debtor, "telegram_id", "") or ""),
        "telegram_access_hash": str(getattr(debtor, "telegram_access_hash", "") or ""),
        "telegram_username": str(getattr(debtor, "telegram_username", "") or ""),
        "contact_name": str(getattr(debtor, "name", "") or "")[:32],
        "text": (text or "").strip(),
    }
    from .tg_outbox import write_job

    write_job(job)
    logger.info(
        "Telegram DM navbatga shop=%s phone=%s",
        job["shop"],
        job["phone"],
    )
    return {"ok": True, "queued": True, "channel": "telegram"}


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
        return "Telegram topilmadi"
    if st == "no_phone":
        return "Telefon yo‘q"
    if st == "error":
        # popular/privacy — hali bor bo‘lishi mumkin, yolg‘on «topilmadi» yo‘q
        return "Tekshirilmoqda"
    return "Tekshirilmagan"


def send_debt_reminder(debtor: ClientDebtor, kind: str) -> dict[str, Any]:
    bal = debtor.balance()
    if bal <= 0 or not debtor.due_date:
        return {"ok": False, "skipped": True, "error": "Qarz yoki sana yo‘q"}
    overdue_days = max(0, (timezone.localdate() - debtor.due_date).days)
    text = build_reminder_text(
        kind=kind,
        shop=_shop_template_label(debtor.shop_key),
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
        if bal <= 0 or (kind != KIND_BALANCE and not debtor.due_date):
            continue
        overdue_days = (
            max(0, (timezone.localdate() - debtor.due_date).days)
            if debtor.due_date
            else 0
        )
        text = build_reminder_text(
            kind=kind,
            shop=_shop_template_label(debtor.shop_key),
            amount=bal,
            due=debtor.due_date,
            overdue_days=overdue_days,
        )
        prepared.append((debtor, kind, text))

    return _run(send_jobs_by_phone_async(prepared), timeout=3600)
