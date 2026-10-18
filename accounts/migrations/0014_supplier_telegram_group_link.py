from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_debtsmstemplate_group_notified"),
    ]

    operations = [
        migrations.AddField(
            model_name="debtsmstemplate",
            name="supplier_telegram_group_link",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Taminotchi kirim/qarz xabarlari: https://t.me/c/<id>/<topic>",
                max_length=255,
            ),
        ),
    ]
