"""SSH orqali TezPOS Setup .exe ni o‘rnatish (admin 500 bo‘lsa)."""
from __future__ import annotations

import os
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from accounts.models import DesktopInstaller


class Command(BaseCommand):
    help = "TezPOS installer .exe ni yuklaydi va faollashtiradi"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Lokal .exe yo‘li, masalan /tmp/TezPOS-Setup.exe")
        parser.add_argument("--title", default="TezPOS Setup")
        parser.add_argument("--ver", default="", dest="app_version", help="Versiya, masalan 1.0.0")

    def handle(self, *args, **options):
        src = Path(options["path"]).expanduser().resolve()
        if not src.is_file():
            raise CommandError(f"Fayl topilmadi: {src}")
        if src.suffix.lower() != ".exe":
            self.stdout.write(self.style.WARNING("Ogohlantirish: kengaytma .exe emas"))

        title = (options["title"] or "TezPOS Setup").strip()[:120]
        version = (options["app_version"] or "").strip()[:40]

        row = DesktopInstaller(title=title, version=version, is_active=True)
        with src.open("rb") as fh:
            row.file.save(src.name, File(fh), save=True)

        self.stdout.write(self.style.SUCCESS(f"OK id={row.pk} file={row.file.name}"))
        self.stdout.write(f"Install URL: /accounts/download/")
