from django.conf import settings
from django.db import models


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

    # Fon smena sync (cron) uchun — login paytida yangilanadi
    tezpos_api_token = models.CharField(max_length=255, blank=True, default="")
    tezpos_server_name = models.CharField(max_length=120, blank=True, default="")

    def __str__(self) -> str:
        return self.business_name


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
