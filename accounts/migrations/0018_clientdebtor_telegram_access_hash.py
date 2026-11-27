from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_debtsmstemplate_remind_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientdebtor",
            name="telegram_access_hash",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
