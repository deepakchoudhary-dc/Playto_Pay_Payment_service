from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchant",
            name="balance_reconciled_ledger_entry_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="merchant",
            name="reconciled_available_balance_paise",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="merchant",
            name="reconciled_held_balance_paise",
            field=models.BigIntegerField(default=0),
        ),
    ]
