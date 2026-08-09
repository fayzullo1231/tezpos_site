"""Telegram Bot API — smena xabarlari va Excel yuborish."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from typing import Any


TG_API = "https://api.telegram.org"


def parse_recipient_line(raw: str) -> str | None:
    """Qatorni Telegram chat_id yoki @username ga aylantiradi."""
    s = (raw or "").strip()
    if not s or s.startswith("#"):
        return None
    s = s.replace("https://", "").replace("http://", "")
    if s.startswith("t.me/"):
        s = s[5:]
    if s.startswith("telegram.me/"):
        s = s[12:]
    s = s.strip().strip("/")

    # Private channel/supergroup: t.me/c/1234567890/11 → -1001234567890
    m = re.match(r"^c/(\d+)(?:/\d+)?$", s)
    if m:
        return f"-100{m.group(1)}"

    # Invite links (joinchat / +) — bot API to'g'ridan-to'g'ri ishlamaydi
    if s.startswith("joinchat/") or s.startswith("+"):
        return None

    if s.startswith("@"):
        return s
    if re.fullmatch(r"-?\d+", s):
        return s
    # Oddiy username
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", s):
        return f"@{s}"
    return None


def parse_recipients(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        chat = parse_recipient_line(line)
        if not chat or chat in seen:
            continue
        seen.add(chat)
        out.append(chat)
    return out


def _api_call(token: str, method: str, payload: dict | None = None, timeout: float = 40) -> dict:
    url = f"{TG_API}/bot{token}/{method}"
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(err_body)
        except json.JSONDecodeError:
            body = {"ok": False, "description": err_body or str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "description": str(exc)}
    return body if isinstance(body, dict) else {"ok": False, "description": "bad response"}


def get_me(token: str) -> dict:
    return _api_call(token, "getMe")


def get_updates(token: str, limit: int = 50) -> dict:
    return _api_call(token, "getUpdates", {"offset": -limit, "limit": limit, "timeout": 0})


def list_recent_chats(token: str) -> list[dict[str, Any]]:
    """Botga yozgan foydalanuvchi/guruh/kanallar (getUpdates)."""
    data = get_updates(token, limit=80)
    if not data.get("ok"):
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for upd in reversed(data.get("result") or []):
        if not isinstance(upd, dict):
            continue
        msg = (
            upd.get("message")
            or upd.get("edited_message")
            or upd.get("channel_post")
            or upd.get("my_chat_member")
            or {}
        )
        chat = msg.get("chat") if isinstance(msg, dict) else None
        if not isinstance(chat, dict):
            continue
        cid = str(chat.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        uname = (chat.get("username") or "").strip()
        title = (chat.get("title") or "").strip()
        first = (chat.get("first_name") or "").strip()
        last = (chat.get("last_name") or "").strip()
        name = title or " ".join(x for x in (first, last) if x) or uname or cid
        out.append(
            {
                "id": cid,
                "username": f"@{uname}" if uname else "",
                "name": name,
                "type": chat.get("type") or "",
            }
        )
        if len(out) >= 25:
            break
    return out


def resolve_chat_id(token: str, recipient: str) -> tuple[str, str | None]:
    """
    @username ni mumkin bo'lsa raqamli chat_id ga aylantiradi.
    Private chat uchun foydalanuvchi botga /start bosgan bo'lishi kerak.
    """
    r = (recipient or "").strip()
    if not r:
        return r, "bo‘sh manzil"
    if re.fullmatch(r"-?\d+", r):
        return r, None

    uname = r[1:] if r.startswith("@") else r
    uname_l = uname.lower()
    for chat in list_recent_chats(token):
        cu = (chat.get("username") or "").lstrip("@").lower()
        if cu and cu == uname_l:
            return str(chat["id"]), None
    return r, None


def explain_tg_error(description: str, recipient: str) -> str:
    d = (description or "").lower()
    if "chat not found" in d:
        return (
            f"{recipient}: chat topilmadi. "
            "Shaxsiy chat uchun avval botga kirib /start bosing, "
            "keyin «Chatlarni topish» orqali raqamli ID ni qo‘ying. "
            "Guruh/kanal uchun botni qo‘shib, admin qiling."
        )
    if "blocked" in d or "deactivated" in d:
        return f"{recipient}: foydalanuvchi botni bloklagan yoki o‘chirilgan."
    if "not enough rights" in d or "need administrator" in d:
        return f"{recipient}: botga yozish huquqi yo‘q — admin qiling."
    if "chat_id is empty" in d:
        return f"{recipient}: chat ID bo‘sh."
    return f"{recipient}: {description or 'xato'}"


def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> dict:
    resolved, _hint = resolve_chat_id(token, chat_id)
    return _api_call(
        token,
        "sendMessage",
        {
            "chat_id": resolved,
            "text": text[:4000],
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
    )


def send_document(
    token: str,
    chat_id: str,
    filename: str,
    file_bytes: bytes,
    caption: str = "",
) -> dict:
    """multipart/form-data orqali Excel yuborish."""
    resolved, _ = resolve_chat_id(token, chat_id)
    chat_id = resolved
    boundary = "----TezPosBoundary7MA4YWxkTrZu0gW"
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption[:1000])
        add_field("parse_mode", "HTML")

    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode()
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = f"{TG_API}/bot{token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(err_body)
        except json.JSONDecodeError:
            return {"ok": False, "description": err_body or str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "description": str(exc)}


def broadcast_message(token: str, recipients: list[str], text: str) -> list[dict[str, Any]]:
    results = []
    for chat in recipients:
        resolved, _ = resolve_chat_id(token, chat)
        res = send_message(token, resolved, text)
        ok = bool(res.get("ok"))
        desc = res.get("description") or ""
        results.append(
            {
                "chat": chat,
                "resolved": resolved,
                "ok": ok,
                "error": "" if ok else explain_tg_error(desc, chat),
                "raw": res,
            }
        )
    return results


def broadcast_document(
    token: str,
    recipients: list[str],
    filename: str,
    file_bytes: bytes,
    caption: str = "",
) -> list[dict[str, Any]]:
    results = []
    for chat in recipients:
        resolved, _ = resolve_chat_id(token, chat)
        res = send_document(token, resolved, filename, file_bytes, caption=caption)
        ok = bool(res.get("ok"))
        desc = res.get("description") or ""
        results.append(
            {
                "chat": chat,
                "resolved": resolved,
                "ok": ok,
                "error": "" if ok else explain_tg_error(desc, chat),
                "raw": res,
            }
        )
    return results


def build_shift_excel(
    *,
    business_name: str,
    shift: dict,
    sales_rows: list[dict],
    price_lists: list[dict],
    credit_rows: list[dict],
    debtors_rows: list[dict] | None = None,
    low_stock_rows: list[dict] | None = None,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Xulosa"
    summary = [
        ("Biznes", business_name),
        ("Smena", shift.get("status_label") or shift.get("status") or ""),
        ("Kassir", shift.get("cashier") or ""),
        ("Ochilgan", shift.get("opened_at_display") or shift.get("opened_at") or ""),
        ("Yopilgan", shift.get("closed_at_display") or shift.get("closed_at") or "—"),
        ("Cheklar", shift.get("checks") or 0),
        ("Jami savdo", shift.get("gross") or 0),
        ("Sof foyda", shift.get("profit") or 0),
        ("Marja %", shift.get("margin") or 0),
        ("Sotuv narxida", shift.get("selling_revenue") or 0),
        ("Narxlar ro‘yxatida (optom)", shift.get("wholesale_revenue") or 0),
        ("Qarzga savdo (smena)", shift.get("credit_total") or 0),
        ("Qarzdorlar (jami)", shift.get("debtors_count") or 0),
        ("Qarzdorlar summasi", shift.get("debtors_total") or 0),
        ("Kam qoldiq (tovar)", shift.get("low_stock_count") or 0),
    ]
    ws.append(["Ko‘rsatkich", "Qiymat"])
    for k, v in summary:
        ws.append([k, v])

    ws2 = wb.create_sheet("Kunlik sotuv")
    ws2.append(["#", "Vaqt", "Mijoz", "To‘lov", "Summa", "Foyda"])
    for i, row in enumerate(sales_rows, start=1):
        ws2.append(
            [
                i,
                row.get("time") or "",
                row.get("customer") or "",
                row.get("payment") or "",
                row.get("total") or 0,
                row.get("profit") or 0,
            ]
        )

    ws3 = wb.create_sheet("Narxlar")
    ws3.append(["Ro‘yxat", "Cheklar", "Tushum", "Foyda", "Ulush %"])
    for row in price_lists:
        ws3.append(
            [
                row.get("name") or "",
                row.get("checks") or 0,
                row.get("revenue") or 0,
                row.get("profit") or 0,
                row.get("share") or 0,
            ]
        )

    ws4 = wb.create_sheet("Smena qarzlari")
    ws4.append(["Mijoz", "Cheklar", "Qarz summa"])
    for row in credit_rows:
        ws4.append([row.get("customer") or "", row.get("orders") or 0, row.get("total") or 0])

    ws5 = wb.create_sheet("Qarzdorlar")
    ws5.append(["#", "Mijoz", "Telefon", "Qarz"])
    for i, row in enumerate(debtors_rows or [], start=1):
        ws5.append(
            [
                i,
                row.get("name") or row.get("customer") or "",
                row.get("phone") or "",
                row.get("debt") or row.get("total") or 0,
            ]
        )

    ws6 = wb.create_sheet("Kam qoldiq")
    ws6.append(["#", "Mahsulot", "Qoldiq", "Minimal"])
    for i, row in enumerate(low_stock_rows or [], start=1):
        ws6.append(
            [
                i,
                row.get("name") or "",
                row.get("stock") if row.get("stock") is not None else row.get("qty") or 0,
                row.get("min_stock") if row.get("min_stock") is not None else "",
            ]
        )

    for sheet in wb.worksheets:
        for col in sheet.columns:
            letter = get_column_letter(col[0].column)
            width = 12
            for cell in col:
                width = max(width, min(48, len(str(cell.value or "")) + 2))
            sheet.column_dimensions[letter].width = width

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def format_money(n: float | int) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.0f}".replace(",", " ")


def build_shift_message(
    *,
    business_name: str,
    event: str,
    shift: dict,
) -> str:
    status = "ochildi" if event == "open" else "yopildi"
    emoji = "🟢" if event == "open" else "🔴"
    lines = [
        f"{emoji} <b>{business_name}</b>",
        f"Smena <b>{status}</b>",
        f"Kassir: <b>{shift.get('cashier') or '—'}</b>",
        f"Vaqt: {shift.get('opened_at_display') or shift.get('opened_display') or shift.get('opened_at') or '—'}"
        + (
            f" → {shift.get('closed_at_display') or shift.get('closed_display') or shift.get('closed_at') or '—'}"
            if event == "close"
            else ""
        ),
        "",
        f"🧾 Cheklar: <b>{int(shift.get('checks') or 0)}</b>",
        f"💰 Savdo: <b>{format_money(shift.get('gross') or 0)}</b> so'm",
        f"📈 Sof foyda: <b>{format_money(shift.get('profit') or 0)}</b> so'm",
        f"📊 Marja: <b>{float(shift.get('margin') or 0):.1f}%</b>",
        "",
        f"🛒 Sotuv narxida: <b>{format_money(shift.get('selling_revenue') or 0)}</b>",
        f"📦 Optom / narxlar ro‘yxati: <b>{format_money(shift.get('wholesale_revenue') or 0)}</b>",
        f"💳 Qarzga (smena): <b>{format_money(shift.get('credit_total') or 0)}</b>",
    ]

    if event == "close":
        lines += [
            "",
            f"👥 Qarzdorlar: <b>{int(shift.get('debtors_count') or 0)}</b> ta · "
            f"<b>{format_money(shift.get('debtors_total') or 0)}</b> so'm",
            f"⚠️ Kam qoldiq: <b>{int(shift.get('low_stock_count') or 0)}</b> ta tovar",
            "",
            "📎 Excel: kunlik sotuv, qarzdorlar, kam qoldiq — faylda.",
        ]

        debtors = shift.get("debtors") or []
        if debtors:
            lines.append("")
            lines.append("<b>Qarzdorlar ro‘yxati:</b>")
            for c in debtors[:15]:
                name = c.get("name") or c.get("customer") or "—"
                lines.append(f"• {name}: {format_money(c.get('debt') or c.get('total') or 0)} so'm")
            if len(debtors) > 15:
                lines.append(f"… yana {len(debtors) - 15} ta (Excelda)")

        low = shift.get("low_stock") or []
        if low:
            lines.append("")
            lines.append("<b>Kam qoldiq:</b>")
            for p in low[:12]:
                lines.append(
                    f"• {p.get('name')}: {p.get('stock')} (min {p.get('min_stock')})"
                )
            if len(low) > 12:
                lines.append(f"… yana {len(low) - 12} ta (Excelda)")
    else:
        credits = shift.get("credit_customers") or []
        if credits:
            lines.append("")
            lines.append("<b>Qarzga sotuv:</b>")
            for c in credits[:12]:
                lines.append(
                    f"• {c.get('customer')}: {format_money(c.get('total') or 0)} so'm"
                )

    text = "\n".join(lines)
    # Telegram limito ~4096
    if len(text) > 4000:
        text = text[:3980] + "\n…"
    return text
