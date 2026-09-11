"""
Serverda Telegram qarz xabarini tekshirish:

  python manage.py test_debt_telegram --phone 998901234567
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from accounts import telethon_debt
from accounts.devsms import build_client_debt_message, normalize_phone


class Command(BaseCommand):
    help = "Telethon orqali qarz SMS matnini test yuborish"

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--text", default="")
        parser.add_argument("--shop", default="Kulol Optom")

    def handle(self, *args, **options):
        phone = normalize_phone(options["phone"])
        if not phone:
            self.stderr.write(self.style.ERROR("Telefon noto‘g‘ri"))
            return
        if not telethon_debt.telethon_configured():
            self.stderr.write(self.style.ERROR("Telethon sozlanmagan (.env)"))
            return

        self.stdout.write(f"api_id={settings.TELETHON_API_ID}")
        self.stdout.write(f"session_len={len(settings.TELETHON_SESSION or '')}")

        text = (options.get("text") or "").strip()
        if not text:
            text = build_client_debt_message(
                shop=options["shop"],
                branch="Oziq ovqat",
                transaction_amount=50000,
                balance=150000,
                check_link="https://tez-pos.uz/",
            )
        self.stdout.write("--- matn ---")
        self.stdout.write(text)
        self.stdout.write("------------")
        self.stdout.write(f"Yuborilmoqda -> +{phone} ...")
        res = telethon_debt.send_text_by_phone(phone, text)
        if res.get("ok"):
            self.stdout.write(
                self.style.SUCCESS(f"OK telegram_id={res.get('telegram_id')}")
            )
        else:
            self.stderr.write(self.style.ERROR(f"FAIL: {res.get('error') or res}"))
