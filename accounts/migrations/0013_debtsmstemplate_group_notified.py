from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_debtsmstemplate_telegram_group_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="debtsmstemplate",
            name="group_notified",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Smena yopilganda qarzdorlar ro‘yxati yuborilgan smenalar",
            ),
        ),
    ]
