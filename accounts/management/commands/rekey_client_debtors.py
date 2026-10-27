from django.core.management.base import BaseCommand

from accounts.models import ClientDebtor, DebtSmsTemplate


class Command(BaseCommand):
    help = "Qarzdorlarni eski shop_key dan yangi server nomiga ko‘chiradi."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="src", required=True)
        parser.add_argument("--to", dest="dst", required=True)
        parser.add_argument("--dry", action="store_true")

    def handle(self, *args, **opts):
        src = (opts["src"] or "").strip().lower()
        dst = (opts["dst"] or "").strip().lower()
        if not src or not dst or src == dst:
            self.stderr.write("from/to noto‘g‘ri")
            return
        qs = ClientDebtor.objects.filter(shop_key=src)
        n = qs.count()
        self.stdout.write(f"{src} -> {dst}: {n} qarzdor")
        if opts["dry"]:
            return
        qs.update(shop_key=dst)
        if not DebtSmsTemplate.objects.filter(shop_key=dst).exists():
            old = DebtSmsTemplate.objects.filter(shop_key=src).first()
            if old:
                old.shop_key = dst
                old.save(update_fields=["shop_key", "updated_at"])
                self.stdout.write("shablon ham ko‘chirildi")
        self.stdout.write("OK")
