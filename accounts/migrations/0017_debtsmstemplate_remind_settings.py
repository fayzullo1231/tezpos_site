from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0016_clientdebtorledger_items"),
    ]

    operations = [
        migrations.AddField(
            model_name="debtsmstemplate",
            name="remind_weekdays",
            field=models.CharField(
                blank=True,
                default="0,1,2,3,4,5,6",
                help_text="Eslatma yuboriladigan hafta kunlari (0=Du … 6=Ya)",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="debtsmstemplate",
            name="remind_overdue_days",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="Qarz kamida shuncha kun kechiksa eslatma (1 = ertasi kundan)",
            ),
        ),
        migrations.AddField(
            model_name="debtsmstemplate",
            name="remind_soon",
            field=models.BooleanField(
                default=True,
                help_text="Qaytarish sanasidan 1 kun oldin eslatma",
            ),
        ),
        migrations.AddField(
            model_name="debtsmstemplate",
            name="remind_due_today",
            field=models.BooleanField(
                default=True,
                help_text="Qaytarish kuni eslatma",
            ),
        ),
    ]
