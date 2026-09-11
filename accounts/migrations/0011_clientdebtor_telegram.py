# Generated manually for ClientDebtor Telegram fields

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_clientdebtor_due_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_id",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_username",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_name",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_status",
            field=models.CharField(
                blank=True,
                default="",
                help_text="ok | no_telegram | no_phone | error",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="tg_last_remind_kind",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="clientdebtor",
            name="tg_last_remind_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
