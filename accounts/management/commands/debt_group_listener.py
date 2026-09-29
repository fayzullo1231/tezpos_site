"""Telegram guruhda /qarz yozilsa — shu guruhga bog‘langan do‘kon qarzdorlari ro‘yxati."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from accounts.client_debts import debtors_summary_chunks
from accounts.models import DebtSmsTemplate
from accounts.telethon_debt import _client, parse_group_link, telethon_configured

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
        if not telethon_configured():
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
        from telethon import events

        client = await _client()
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
            shops = _match_shops(
                linked,
                chat_id,
                getattr(chat, "username", "") or "",
                _message_topic(event.message),
            )
            if not shops:
                return
            last_reply[chat_id] = now
            for shop in shops:
                chunks = await sync_to_async(debtors_summary_chunks)(shop)
                for chunk in chunks:
                    await event.reply(chunk, parse_mode="html", link_preview=False)
            logger.info("/qarz javob berildi chat=%s shops=%s", chat_id, shops)

        self.stdout.write("debt_group_listener: /qarz kutilmoqda")
        try:
            await client.run_until_disconnected()
        finally:
            await client.disconnect()
