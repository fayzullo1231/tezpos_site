"""Telegram guruhda /qarz + qarzdorga shaxsiy xabar (userbot session shu yerda)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from accounts.client_debts import debtors_summary_chunks
from accounts.models import ClientDebtor, DebtSmsTemplate
from accounts import devsms
from accounts.telethon_debt import (
    _client_from_creds,
    _send_by_phone,
    apply_telegram_meta,
    describe_sessions,
    parse_group_link,
    send_group_on_client,
    session_groups,
)
from accounts.tg_outbox import mark_done, mark_fail, take_jobs

logger = logging.getLogger("tezpos.telegram")

COMMAND_RE = re.compile(r"^/qarz(?:@\w+)?\s*$", re.IGNORECASE)
COOLDOWN_SEC = 10
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 18765


def _linked_shops() -> list[tuple[str, dict]]:
    out = []
    for shop, link in DebtSmsTemplate.objects.exclude(telegram_group_link="").values_list(
        "shop_key", "telegram_group_link"
    ):
        info = parse_group_link(link)
        if info:
            out.append((shop, info))
    return out


def _message_topic(message) -> int | None:
    rt = getattr(message, "reply_to", None)
    if not rt or not getattr(rt, "forum_topic", False):
        return None
    return getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)


def _match_shops(linked, chat_id: int, username: str, topic: int | None) -> list[str]:
    in_chat = []
    for shop, info in linked:
        chat = info["chat"]
        if isinstance(chat, int):
            if chat_id not in (chat, int(f"-100{chat}")):
                continue
        elif not username or chat.lower() != username.lower():
            continue
        in_chat.append((shop, info))
    if topic is not None:
        by_topic = [s for s, i in in_chat if i.get("topic") == topic]
        if by_topic:
            return by_topic
    return [s for s, i in in_chat if not i.get("topic") or topic is None] or [
        s for s, _ in in_chat
    ]


def _norm_shops(shops) -> set[str]:
    return {str(s or "").strip().lower() for s in (shops or []) if str(s or "").strip()}


def _pick_sender(shop: str, senders: dict):
    if shop and shop in senders:
        return senders[shop]
    if shop:
        for key, val in senders.items():
            if shop.startswith(key) or key.startswith(shop):
                return val
        if shop == "usmon" or shop.startswith("usmon") or shop in ("importproduct", "importoptom"):
            for key, val in senders.items():
                if key == "usmon" or str(key).startswith("usmon") or key in ("importproduct", "importoptom"):
                    return val
    if len(senders) == 1:
        return next(iter(senders.values()))
    return None


class Command(BaseCommand):
    help = "Telegram guruhda /qarz + HTTP orqali shaxsiy qarz xabari"

    def handle(self, *args, **options):
        try:
            for line in describe_sessions():
                print(line, flush=True)
        except Exception:
            logger.exception("telegram_diag yozilmadi")
        while True:
            try:
                groups = session_groups()
                if not groups:
                    self.stderr.write("Telethon hozircha yo‘q, 15s kutamiz")
                    time.sleep(15)
                    continue
                asyncio.run(self._listen())
            except KeyboardInterrupt:
                return
            except Exception:
                logger.exception("debt_group_listener uzildi, 15s dan keyin qayta ulanadi")
            time.sleep(15)

    async def _listen(self):
        groups = await sync_to_async(session_groups)()
        if not groups:
            raise RuntimeError("Telethon sozlanmagan")
        loop = asyncio.get_running_loop()
        senders: dict[str, object] = {}

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                if self.path.rstrip("/") != "/send":
                    self.send_response(404)
                    self.end_headers()
                    return
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    job = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"json"}')
                    return
                shop = str((job or {}).get("shop") or "").strip().lower()
                fn = _pick_sender(shop, senders)
                if fn is None:
                    err = (
                        "Komron userbot ochilmagan (shop="
                        + (shop or "-")
                        + "). python manage.py telegram_diag"
                    )
                    payload = json.dumps(
                        {"ok": False, "error": err, "offline": True},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                holder: dict = {}
                done = threading.Event()

                async def _run():
                    try:
                        holder["res"] = await fn(job or {})
                    except Exception as exc:
                        holder["res"] = {"ok": False, "error": str(exc)[:200]}
                    finally:
                        done.set()

                asyncio.run_coroutine_threadsafe(_run(), loop)
                done.wait(timeout=45)
                res = holder.get("res") or {"ok": False, "error": "timeout"}
                payload = json.dumps(res, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if res.get("ok") else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), _Handler)
        threading.Thread(target=httpd.serve_forever, name="tg-http", daemon=True).start()
        print(f"debt_group_listener HTTP {HTTP_HOST}:{HTTP_PORT}", flush=True)
        for g in groups:
            print(
                "debt_group_listener group shops="
                + ",".join(sorted(g.get("shops") or [])),
                flush=True,
            )

        known = {
            f"{g['api_id']}:{str(g.get('session') or '')[:32]}" for g in groups
        }

        async def _watch_new_groups():
            while True:
                await asyncio.sleep(12)
                try:
                    now = await sync_to_async(session_groups)()
                except Exception:
                    logger.exception("session_groups yangilanmadi")
                    continue
                for g in now:
                    fp = f"{g['api_id']}:{str(g.get('session') or '')[:32]}"
                    if fp in known:
                        continue
                    known.add(fp)
                    print(
                        "yangi userbot shops="
                        + ",".join(sorted(g.get("shops") or [])),
                        flush=True,
                    )
                    asyncio.create_task(self._listen_one(g, senders))

        try:
            await asyncio.gather(
                _watch_new_groups(),
                *(self._listen_one(g, senders) for g in groups),
            )
        finally:
            try:
                httpd.shutdown()
            except Exception:
                pass

    async def _listen_one(self, group: dict, senders: dict):
        while True:
            try:
                await self._run_one(group, senders)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                name = type(exc).__name__
                msg = str(exc)
                dead = (
                    "AuthKeyDuplicated" in name
                    or "AuthKeyUnregistered" in name
                    or "o‘chgan" in msg
                    or "ochgan" in msg
                )
                logger.exception(
                    "userbot uzildi shops=%s",
                    ",".join(group.get("shops") or []),
                )
                if dead:
                    old_fp = f"{group.get('api_id')}:{str(group.get('session') or '')[:32]}"
                    print(
                        "SESSION O'LDI shops="
                        + ",".join(group.get("shops") or [])
                        + " — yangi session saqlansa avtomatik olinadi.",
                        flush=True,
                    )
                    for _ in range(40):
                        await asyncio.sleep(15)
                        try:
                            now = await sync_to_async(session_groups)()
                        except Exception:
                            continue
                        for g in now:
                            shops = set(str(s).lower() for s in (g.get("shops") or []))
                            if not shops.intersection(_norm_shops(group.get("shops"))):
                                continue
                            fp = f"{g['api_id']}:{str(g.get('session') or '')[:32]}"
                            if fp != old_fp:
                                group.update(g)
                                print(
                                    "Yangi session topildi shops="
                                    + ",".join(sorted(shops)),
                                    flush=True,
                                )
                                break
                        else:
                            continue
                        break
                    continue
            await asyncio.sleep(12)

    async def _run_one(self, group: dict, senders: dict):
        from telethon import events

        allowed = _norm_shops(group.get("shops"))
        last_reply: dict[int, float] = {}
        ready = asyncio.Event()
        box: dict = {"client": None}

        async def _send_dm_job(job: dict) -> dict:
            if not ready.is_set():
                try:
                    await asyncio.wait_for(ready.wait(), timeout=25)
                except asyncio.TimeoutError:
                    return {"ok": False, "error": "Userbot hali ulanmagan"}
            client = box.get("client")
            if client is None:
                return {"ok": False, "error": "Userbot yo‘q"}
            phone = await sync_to_async(devsms.normalize_phone)(job.get("phone") or "")
            debtor = None
            pk = job.get("debtor_id")
            if pk:
                debtor = await sync_to_async(
                    ClientDebtor.objects.filter(pk=int(pk)).first
                )()
                if debtor and not phone:
                    phone = await sync_to_async(devsms.normalize_phone)(debtor.phone)
            if not phone:
                return {"ok": False, "error": "Telefon yo‘q"}
            prefer = str(
                job.get("telegram_id") or getattr(debtor, "telegram_id", "") or ""
            )
            res = await _send_by_phone(
                client,
                phone,
                str(job.get("text") or ""),
                prefer_telegram_id=prefer,
            )
            meta = res.get("meta") or {}
            if debtor is not None and meta:
                await apply_telegram_meta(debtor, meta)
            if not res.get("ok"):
                return {"ok": False, "error": str(res.get("error") or "yuborilmadi")}
            print(
                f"Telegram DM ketdi shop={job.get('shop')} phone={phone}",
                flush=True,
            )
            logger.info("Telegram DM ketdi shop=%s phone=%s", job.get("shop"), phone)
            return {"ok": True, "phone": phone}

        for shop in allowed:
            senders[shop] = _send_dm_job
        if any(s == "usmon" or s.startswith("usmon") for s in allowed):
            senders["usmon"] = _send_dm_job

        client = await _client_from_creds(
            group["api_id"], group["api_hash"], group["session"]
        )
        box["client"] = client
        ready.set()
        print(
            "debt_group_listener shops=" + ",".join(sorted(allowed)),
            flush=True,
        )

        @client.on(events.NewMessage(pattern=COMMAND_RE))
        async def _on_qarz(event):
            chat_id = event.chat_id
            now = time.time()
            if now - last_reply.get(chat_id, 0) < COOLDOWN_SEC:
                return
            linked = await sync_to_async(_linked_shops)()
            if not linked:
                return
            chat = await event.get_chat()
            shops = [
                s
                for s in _match_shops(
                    linked,
                    chat_id,
                    getattr(chat, "username", "") or "",
                    _message_topic(event.message),
                )
                if (s or "").strip().lower() in allowed
            ]
            if not shops:
                return
            last_reply[chat_id] = now
            for shop in shops:
                chunks = await sync_to_async(debtors_summary_chunks)(shop)
                for chunk in chunks:
                    await event.reply(chunk, parse_mode="html", link_preview=False)
            logger.info("/qarz javob berildi chat=%s shops=%s", chat_id, shops)

        async def _drain_outbox():
            nonlocal allowed
            while True:
                try:

                    def _refresh():
                        fp = f"{group['api_id']}:{str(group.get('session') or '')[:32]}"
                        found = allowed
                        for g in session_groups():
                            if f"{g['api_id']}:{str(g.get('session') or '')[:32]}" == fp:
                                found = _norm_shops(g.get("shops"))
                                break
                        return found

                    allowed = await sync_to_async(_refresh)()
                    for shop in allowed:
                        senders[shop] = _send_dm_job
                    if any(s == "usmon" or s.startswith("usmon") for s in allowed):
                        senders["usmon"] = _send_dm_job
                    jobs = await sync_to_async(take_jobs)(allowed)
                    for path, job in jobs:
                        err = ""
                        try:
                            kind = str(job.get("type") or "")
                            if kind == "dm":
                                res = await _send_dm_job(job)
                                if not res.get("ok"):
                                    err = str(res.get("error") or "yuborilmadi")
                            elif kind == "group":
                                res = await send_group_on_client(
                                    box["client"],
                                    str(job.get("link") or ""),
                                    str(job.get("text") or ""),
                                )
                                if not res.get("ok"):
                                    err = str(res.get("error") or "guruhga yozilmadi")
                                else:
                                    print(
                                        f"Telegram guruh ketdi shop={job.get('shop')}",
                                        flush=True,
                                    )
                            else:
                                err = "Noma’lum job"
                        except Exception as exc:
                            logger.exception("outbox job xato")
                            err = str(exc)[:200]
                        if err:
                            print(
                                f"Telegram outbox fail shop={job.get('shop')} err={err}",
                                flush=True,
                            )
                            await sync_to_async(mark_fail)(path, err)
                        else:
                            await sync_to_async(mark_done)(path)
                except Exception:
                    logger.exception("outbox drain xato")
                await asyncio.sleep(0.8)

        outbox_task = asyncio.create_task(_drain_outbox())
        try:
            await client.run_until_disconnected()
        finally:
            outbox_task.cancel()
            ready.clear()
            box["client"] = None
            try:
                await client.disconnect()
            except Exception:
                pass
