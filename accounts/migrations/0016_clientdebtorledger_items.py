from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_debtsms_sms_userbot"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientdebtorledger",
            name="items",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Savatdagi mahsulotlar (POS qarzga savdo)",
            ),
        ),
    ]
