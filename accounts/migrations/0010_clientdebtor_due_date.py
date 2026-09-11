# Generated manually for ClientDebtor.due_date

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_desktop_installer_file_blank"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientdebtor",
            name="due_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text="Qarz qaytarish sanasi",
                null=True,
            ),
        ),
    ]
