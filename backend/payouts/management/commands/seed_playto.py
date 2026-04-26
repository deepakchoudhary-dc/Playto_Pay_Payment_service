from django.core.management.base import BaseCommand
from django.db import transaction

from payouts.models import BankAccount, LedgerEntry, Merchant
from payouts.services import reconcile_materialized_balances


SEED_MERCHANTS = [
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "name": "Blue Mango Studio",
        "email": "finance@bluemango.example",
        "bank": ("HDFC Bank", "XXXXXX4821", "HDFC0001234"),
        "credits": [
            (180_000, "Invoice BM-1001 paid by Orbit Labs"),
            (95_000, "Invoice BM-1002 paid by Northstar Inc"),
            (220_000, "Invoice BM-1003 paid by Kestrel AI"),
        ],
    },
    {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "Kiteframe Consulting",
        "email": "ops@kiteframe.example",
        "bank": ("ICICI Bank", "XXXXXX9044", "ICIC0000456"),
        "credits": [
            (75_000, "Retainer paid by Mesa Cloud"),
            (160_000, "Milestone paid by Jasper Systems"),
        ],
    },
    {
        "id": "33333333-3333-4333-8333-333333333333",
        "name": "SignalCart",
        "email": "founders@signalcart.example",
        "bank": ("Axis Bank", "XXXXXX3370", "UTIB0000789"),
        "credits": [
            (42_500, "Subscription batch from US customers"),
            (117_500, "Subscription batch from EU customers"),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed demo merchants, bank accounts, and credit ledger history."

    @transaction.atomic
    def handle(self, *args, **options):
        for seed in SEED_MERCHANTS:
            merchant, _ = Merchant.objects.update_or_create(
                id=seed["id"],
                defaults={"name": seed["name"], "email": seed["email"]},
            )
            bank_name, masked_account_number, ifsc = seed["bank"]
            BankAccount.objects.update_or_create(
                merchant=merchant,
                masked_account_number=masked_account_number,
                defaults={
                    "beneficiary_name": merchant.name,
                    "bank_name": bank_name,
                    "ifsc": ifsc,
                    "is_active": True,
                },
            )
            if not LedgerEntry.objects.filter(merchant=merchant).exists():
                LedgerEntry.objects.bulk_create(
                    [
                        LedgerEntry(
                            merchant=merchant,
                            amount_paise=amount,
                            direction=LedgerEntry.Direction.CREDIT,
                            bucket=LedgerEntry.Bucket.AVAILABLE,
                            reason=LedgerEntry.Reason.CUSTOMER_PAYMENT,
                            description=description,
                        )
                        for amount, description in seed["credits"]
                    ]
                )
            balances = reconcile_materialized_balances(merchant.id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Seeded {merchant.name}: available={balances['available_balance_paise']} held={balances['held_balance_paise']}"
                )
            )
