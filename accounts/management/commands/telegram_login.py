"""
Komron String sessionni SHU serverda yaratadi (bir IP).
Contabo:

  cd /opt/tezpos_site && source .venv/bin/activate
  python manage.py telegram_login --shop usmon

Telefon kodi Komron Telegramiga keladi. 2FA bo‘lsa parol so‘raydi.
Keyin: systemctl restart tezpos-debt-listener
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from accounts.models import DebtSmsTemplate
from accounts.telethon_debt import _clean_cred, _creds_from_tpl, _creds_ok, _global_creds


class Command(BaseCommand):
    help = "Usmon/Komron uchun yangi String sessionni serverda yaratadi"

    def add_arguments(self, parser):
        parser.add_argument("--shop", default="usmon")

    def handle(self, *args, **options):
        shop = (options.get("shop") or "usmon").strip().lower()
        if shop in ("importproduct", "importoptom"):
            shop = "usmon"
        tpl = DebtSmsTemplate.objects.filter(shop_key__iexact=shop).first()
        if tpl is None and shop != "usmon":
            tpl = DebtSmsTemplate.objects.filter(shop_key__iexact="usmon").first()
            if tpl is not None:
                shop = "usmon"
        if tpl is None:
            tpl = DebtSmsTemplate(shop_key=shop)
        api_id, api_hash, _old = _creds_from_tpl(tpl)
        g_id, g_hash, _g = _global_creds()
        if not api_id or not str(api_id).isdigit():
            api_id = _clean_cred(tpl.telethon_api_id) or g_id
        if not api_hash:
            api_hash = _clean_cred(tpl.telethon_api_hash) or g_hash
        if not api_id or not api_hash or not str(api_id).isdigit():
            self.stderr.write(
                "API ID/Hash yo‘q. Avval saytda Usmon Shabloniga API ni yozing."
            )
            return

        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient

        self.stdout.write(
            f"shop={shop} api_id={api_id}. Telefon +998... formatida."
        )
        self.stdout.write("Kod faqat shu serverda kiritiladi. Boshqa joyda session ochilmasin.")
        client = TelegramClient(StringSession(), int(api_id), str(api_hash))
        client.start()
        session = client.session.save()
        me = client.get_me()
        client.disconnect()
        username = (getattr(me, "username", None) or "") or ""
        first = (getattr(me, "first_name", None) or "") or ""
        last = (getattr(me, "last_name", None) or "") or ""
        display = ("@" + username) if username else (first + " " + last).strip() or "Komron"
        tpl.telethon_api_id = str(api_id)[:32]
        tpl.telethon_api_hash = str(api_hash)[:80]
        tpl.telethon_session = session
        tpl.telethon_user = display[:180]
        tpl.save()
        if not _creds_ok(str(api_id), str(api_hash), session):
            self.stderr.write("Session saqlanmadi.")
            return
        self.stdout.write(self.style.SUCCESS(f"Saqlandi: {display}"))
        self.stdout.write("Keyin: systemctl restart tezpos-debt-listener")
