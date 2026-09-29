from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_clientdebtor_telegram"),
    ]

    operations = [
        migrations.AddField(
            model_name="debtsmstemplate",
            name="telegram_group_link",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Qarz qo‘shilsa/ayirilsa xabar boradigan guruh: https://t.me/c/<id>/<topic>",
                max_length=255,
            ),
        ),
    ]
