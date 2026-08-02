from datetime import timedelta
from decimal import Decimal
from random import choice, randint, uniform

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import TenantProfile
from billing.models import Plan, Subscription
from catalog.models import Product
from sales.models import Sale, SaleItem


class Command(BaseCommand):
    help = "Create demo user, products, plans and sales for TezPOS cabinet"

    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"email": "demo@tezpos.uz", "first_name": "Demo", "last_name": "Admin"},
        )
        user.set_password("demo12345")
        user.is_staff = True
        user.save()

        tenant, _ = TenantProfile.objects.get_or_create(
            user=user,
            defaults={
                "business_name": "TezPOS Demo Market",
                "phone": "+998712002020",
                "address": "Toshkent, Chilonzor",
            },
        )
        tenant.business_name = "TezPOS Demo Market"
        tenant.save()

        plans = [
            ("Starter", Decimal("0"), 1, 500),
            ("Professional", Decimal("150000"), 3, 5000),
            ("Business", Decimal("300000"), 20, 50000),
        ]
        for name, price, users, products in plans:
            Plan.objects.update_or_create(
                name=name,
                defaults={
                    "monthly_price": price,
                    "max_users": users,
                    "max_products": products,
                    "is_active": True,
                },
            )

        today = timezone.localdate()
        pro_plan = Plan.objects.filter(name="Professional").first() or Plan.objects.first()
        if pro_plan:
            Subscription.objects.update_or_create(
                tenant=tenant,
                defaults={
                    "plan": pro_plan,
                    "starts_at": today - timedelta(days=25),
                    "ends_at": today + timedelta(days=5),
                    "status": "active",
                    "auto_renew": True,
                },
            )

        catalog = [
            # name, sell, wholesale, cost, qty, expire_in, image seed
            ("Futbolka Classic", "120000", "95000", "70000", "45", 14),
            ("Krossovka Sport", "450000", "360000", "280000", "18", None),
            ("Sumka City", "220000", "175000", "140000", "12", 5),
            ("Kepka Urban", "65000", "48000", "30000", "60", None),
            ("Shim Slim", "180000", "140000", "95000", "25", None),
            ("Ko'ylak Office", "210000", "165000", "110000", "8", 3),
            ("Shim Jeans", "250000", "190000", "130000", "4", None),
            ("Aksessuar Belt", "85000", "62000", "40000", "30", None),
        ]
        products = []
        for name, sell, wholesale, cost, qty, expire_in in catalog:
            sku = name[:8].upper().replace(" ", "")
            defaults = {
                "selling_price": Decimal(sell),
                "wholesale_price": Decimal(wholesale),
                "cost_price": Decimal(cost),
                "stock_qty": Decimal(qty),
                "min_stock": Decimal("5"),
                "sku": sku,
                "barcode": f"460{randint(1000000, 9999999)}",
                "image_url": f"https://picsum.photos/seed/{sku}/240/240",
                "is_active": True,
            }
            if expire_in is not None:
                defaults["expires_at"] = today + timedelta(days=expire_in)
            else:
                defaults["expires_at"] = None
            product, _ = Product.objects.update_or_create(
                tenant=tenant,
                name=name,
                defaults=defaults,
            )
            products.append(product)

        if Sale.objects.filter(tenant=tenant).count() < 80:
            methods = ["cash", "card", "click", "payme"]
            names = ["Aliyev A.", "Karimova S.", "Toshmatov B.", "Rahimova N.", "Usmonov D.", ""]
            now = timezone.now()
            for i in range(90):
                day = now - timedelta(days=randint(0, 360), hours=randint(0, 20))
                picked = [choice(products) for _ in range(randint(1, 3))]
                total = Decimal("0")
                total_cost = Decimal("0")
                sale = Sale.objects.create(
                    tenant=tenant,
                    created_at=day,
                    customer_name=choice(names),
                    total_amount=Decimal("0"),
                    total_cost=Decimal("0"),
                    payment_method=choice(methods),
                )
                Sale.objects.filter(pk=sale.pk).update(created_at=day)
                for product in picked:
                    qty = Decimal(randint(1, 2))
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        qty=qty,
                        unit_price=product.selling_price,
                        unit_cost=product.cost_price,
                    )
                    total += qty * product.selling_price
                    total_cost += qty * product.cost_price
                total = (total * Decimal(str(round(uniform(0.95, 1.05), 2)))).quantize(Decimal("0.01"))
                Sale.objects.filter(pk=sale.pk).update(
                    total_amount=total,
                    total_cost=total_cost,
                    created_at=day,
                )

        self.stdout.write(self.style.SUCCESS("Demo tayyor: login=demo  parol=demo12345"))
        self.stdout.write(self.style.SUCCESS(f"Sales: {Sale.objects.filter(tenant=tenant).count()}"))
