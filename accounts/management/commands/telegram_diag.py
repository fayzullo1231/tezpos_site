from django.core.management.base import BaseCommand

from accounts.telethon_debt import describe_sessions


class Command(BaseCommand):
    help = "Qaysi do‘kon userboti ochilishini ko‘rsatadi (Komron/usmon)"

    def handle(self, *args, **options):
        self.stdout.write("--- telegram_diag ---")
        for line in describe_sessions():
            self.stdout.write(line)
        self.stdout.write("--- /telegram_diag ---")
