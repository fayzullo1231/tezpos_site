from django.db import migrations


class Migration(migrations.Migration):
    """0017 (remind) va 0018 (telegram_access_hash) parallel bo‘lgan — birlashtirish."""

    dependencies = [
        ("accounts", "0017_debtsmstemplate_remind_settings"),
        ("accounts", "0018_clientdebtor_telegram_access_hash"),
    ]

    operations = []
