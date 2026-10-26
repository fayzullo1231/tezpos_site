"""Telegram guruhda /qarz + qarzdorga shaxsiy xabar (userbot session shu yerda)."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from accounts.client_debts import debtors_summary_chunks
from accounts.models import ClientDebtor, DebtSmsTemplate
from accounts import devsms
from accounts.telethon_debt import (
    _client_from_creds,
    _send_by_phone,
    apply_telegram_meta,
    parse_group_link,
    send_group_on_client,
    session_groups,
)
from accounts.tg_outbox import mark_done, mark_fail, take_jobs

logger = logging.getLogger("tezpos.telegram")

COMMAND_RE = re.compile(r"^/qarz(?:@\w+)?\s*$", re.IGNORECASE)
COOLDOWN_SEC = 10


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


class Command(BaseCommand):
    help = "Telegram guruhda /qarz buyrug‘iga qarzdorlar ro‘yxati bilan javob beradi"

    def handle(self, *args, **options):
        groups = session_groups()
        if not groups:
            self.stderr.write("Telethon sozlanmagan")
            return
        while True:
            try:
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
        await asyncio.gather(*(self._listen_one(g) for g in groups))

    async def _listen_one(self, group: dict):
        from telethon import events

        client = await _client_from_creds(
            group["api_id"], group["api_hash"], group["session"]
        )
        allowed = set(group.get("shops") or [])
        last_reply: dict[int, float] = {}

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
                if s in allowed
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
            while True:
                try:
                    jobs = await sync_to_async(take_jobs)(allowed)
                    for path, job in jobs:
                        err = ""
                        try:
                            kind = str(job.get("type") or "")
                            if kind == "dm":
                                phone = await sync_to_async(devsms.normalize_phone)(
                                    job.get("phone") or ""
                                )
                                debtor = None
                                pk = job.get("debtor_id")
                                if pk:
                                    debtor = await sync_to_async(
                                        ClientDebtor.objects.filter(pk=int(pk)).first
                                    )()
                                    if debtor and not phone:
                                        phone = await sync_to_async(devsms.normalize_phone)(
                                            debtor.phone
                                        )
                                if not phone:
                                    err = "Telefon yo‘q"
                                else:
                                    prefer = str(
                                        job.get("telegram_id")
                                        or getattr(debtor, "telegram_id", "")
                                        or ""
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
                                        err = str(res.get("error") or "yuborilmadi")
                                    else:
                                        logger.info(
                                            "Telegram DM ketdi shop=%s phone=%s",
                                            job.get("shop"),
                                            phone,
                                        )
                            elif kind == "group":
                                res = await send_group_on_client(
                                    client,
                                    str(job.get("link") or ""),
                                    str(job.get("text") or ""),
                                )
                                if not res.get("ok"):
                                    err = str(res.get("error") or "guruhga yozilmadi")
                                else:
                                    logger.info(
                                        "Telegram guruh ketdi shop=%s", job.get("shop")
                                    )
                            else:
                                err = "Noma’lum job"
                        except Exception as exc:
                            logger.exception("outbox job xato")
                            err = str(exc)[:200]
                        if err:
                            logger.warning(
                                "Telegram outbox fail shop=%s type=%s err=%s",
                                job.get("shop"),
                                job.get("type"),
                                err,
                            )
                            await sync_to_async(mark_fail)(path, err)
                        else:
                            await sync_to_async(mark_done)(path)
                except Exception:
                    logger.exception("outbox drain xato")
                await asyncio.sleep(1.2)

        self.stdout.write(
            "debt_group_listener: /qarz + DM shops=" + ",".join(sorted(allowed))
        )
        outbox_task = asyncio.create_task(_drain_outbox())
        try:
            await client.run_until_disconnected()
        finally:
            outbox_task.cancel()
            await client.disconnect()
