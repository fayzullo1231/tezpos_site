from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class TenantProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_profile"
    )
    business_name = models.CharField(max_length=180)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Telegram bot
    telegram_bot_token = models.CharField(max_length=160, blank=True, default="")
    telegram_recipients = models.TextField(
        blank=True,
        default="",
        help_text="Har qator: chat id, @username, guruh/kanal linki",
    )
    telegram_enabled = models.BooleanField(default=False)
    telegram_notify_open = models.BooleanField(default=True)
    telegram_notify_close = models.BooleanField(default=True)
    telegram_notified_events = models.JSONField(default=dict, blank=True)
    telegram_last_sync = models.JSONField(default=dict, blank=True)

    # Fon smena sync (cron) uchun — login paytida yangilanadi
    tezpos_api_token = models.CharField(max_length=255, blank=True, default="")
    tezpos_server_name = models.CharField(max_length=120, blank=True, default="", db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["telegram_enabled", "tezpos_server_name"], name="acct_tenant_tg_sync_idx"),
        ]

    def __str__(self) -> str:
        return self.business_name


class LabelTemplate(models.Model):
    """Narx belgisi shabloni — bir TezPOS do‘koni (server_name) uchun umumiy."""

    shop_key = models.CharField(max_length=140, unique=True, db_index=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=180, blank=True, default="")

    class Meta:
        verbose_name = "Narx belgisi shabloni"
        verbose_name_plural = "Narx belgisi shablonlari"

    def __str__(self) -> str:
        return f"Label template {self.shop_key}"


class DesktopInstaller(models.Model):
    """Saytdagi Install tugmasi uchun .exe — Django admin orqali yuklanadi."""

    title = models.CharField(max_length=120, default="TezPOS Setup")
    version = models.CharField(max_length=40, blank=True, default="")
    file = models.FileField(upload_to="installers/")
    is_active = models.BooleanField(
        default=True,
        help_text="Faol bo‘lsa, saytdagi Install shu faylni yuklaydi.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Desktop installer (.exe)"
        verbose_name_plural = "Desktop installerlar"

    def __str__(self) -> str:
        ver = f" v{self.version}" if self.version else ""
        return f"{self.title}{ver}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            DesktopInstaller.objects.filter(is_active=True).exclude(pk=self.pk).update(
                is_active=False
            )

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).exclude(file="").first()


class Supplier(models.Model):
    """Do‘kon bo‘yicha taminotchilar (TezPOS server_name = shop_key)."""

    shop_key = models.CharField(max_length=140, db_index=True)
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=40, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["shop_key", "name"], name="acct_supplier_shop_name_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["shop_key", "name"],
                name="acct_supplier_shop_name_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def balance(self) -> Decimal:
        """
        >0 — biz taminotchiga qarzdamiz (qizil)
        <0 — taminotchi bizdan qarz (yashil)
        """
        total = self.ledger.aggregate(s=Sum("signed_amount"))["s"]
        return total if total is not None else Decimal("0")


class SupplierProduct(models.Model):
    """Taminotchiga ixtiyoriy mahsulot biriktirish."""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="products"
    )
    product_id = models.CharField(max_length=64, blank=True, default="")
    product_name = models.CharField(max_length=220)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["product_name"]
        indexes = [
            models.Index(
                fields=["supplier", "product_name"],
                name="acct_supprod_name_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "product_id", "product_name"],
                name="acct_supprod_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_id}: {self.product_name}"


class SupplierLedger(models.Model):
    """
    Taminotchi qarz/to‘lov tarixi.
    signed_amount: + biz qarz / ular to‘ladi; − ular qarz / biz to‘ladik.
    """

    KIND_WE_OWE = "we_owe"  # biz qarz oldik
    KIND_THEY_OWE = "they_owe"  # taminotchi bizdan qarz
    KIND_WE_PAY = "we_pay"  # biz pul berdık
    KIND_THEY_PAY = "they_pay"  # ular pul berdı

    KIND_CHOICES = (
        (KIND_WE_OWE, "Biz qarz oldik"),
        (KIND_THEY_OWE, "Taminotchi qarz"),
        (KIND_WE_PAY, "Biz to‘ladik"),
        (KIND_THEY_PAY, "Ular to‘ladi"),
    )

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="ledger"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    signed_amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=180, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["supplier", "-created_at"],
                name="acct_supled_sup_dt_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_id} {self.kind} {self.amount}"

    @staticmethod
    def sign_for(kind: str, amount: Decimal) -> Decimal:
        amt = abs(amount)
        if kind in (SupplierLedger.KIND_WE_OWE, SupplierLedger.KIND_THEY_PAY):
            return amt
        return -amt


class ClientDebtor(models.Model):
    """Mahalliy mijoz qarzdorlari — qo‘shish/ayirish (DevSMS bilan)."""

    shop_key = models.CharField(max_length=140, db_index=True)
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=40, blank=True, default="")
    note = models.CharField(
        max_length=180,
        blank=True,
        default="",
        help_text="Do‘kon / izoh (masalan: Kokcha market)",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["shop_key", "name"], name="acct_clidebt_shop_name_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    def balance(self) -> Decimal:
        total = self.ledger.aggregate(s=Sum("signed_amount"))["s"]
        return total if total is not None else Decimal("0")


class ClientDebtorLedger(models.Model):
    """Mijoz qarziga qo‘shish (+) yoki ayirish (−)."""

    KIND_ADD = "add"
    KIND_SUB = "sub"
    KIND_CHOICES = (
        (KIND_ADD, "Qarz qo‘shildi"),
        (KIND_SUB, "Qarz ayirildi"),
    )

    debtor = models.ForeignKey(
        ClientDebtor, on_delete=models.CASCADE, related_name="ledger"
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    signed_amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=180, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    sms_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["debtor", "-created_at"],
                name="acct_clidebt_led_dt_idx",
            ),
        ]

    @staticmethod
    def sign_for(kind: str, amount: Decimal) -> Decimal:
        amt = abs(amount)
        return amt if kind == ClientDebtorLedger.KIND_ADD else -amt


class DebtSmsTemplate(models.Model):
    """DevSMS qarz shabloni — do‘kon bo‘yicha."""

    shop_key = models.CharField(max_length=140, unique=True, db_index=True)
    title = models.CharField(max_length=120, default="Qarzdorlik")
    shop_label = models.CharField(
        max_length=180,
        blank=True,
        default="",
        help_text="SMS boshidagi do‘kon nomi (bo‘sh bo‘lsa biznes nomi)",
    )
    body = models.TextField(
        default=(
            "{shop}:\n"
            "Qarzdorligingiz : {amount} so'm.\n"
            "Iltimos, qarzdorlikni to'lashni unutmang."
        )
    )
    is_approved = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "DevSMS qarz shabloni"
        verbose_name_plural = "DevSMS qarz shablonlari"

    def __str__(self) -> str:
        return f"{self.shop_key}: {self.title}"

    DEFAULT_BODY = (
        "{shop}:\n"
        "Qarzdorligingiz : {amount} so'm.\n"
        "Iltimos, qarzdorlikni to'lashni unutmang."
    )
