from rest_framework import serializers

from .models import BankAccount, LedgerEntry, Merchant, Payout


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ["id", "name", "email", "available_balance_paise", "held_balance_paise"]


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ["id", "beneficiary_name", "bank_name", "masked_account_number", "ifsc", "is_active"]


class LedgerEntrySerializer(serializers.ModelSerializer):
    payout_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = LedgerEntry
        fields = [
            "id",
            "payout_id",
            "amount_paise",
            "direction",
            "bucket",
            "reason",
            "description",
            "created_at",
        ]


class PayoutSerializer(serializers.ModelSerializer):
    bank_account = BankAccountSerializer(read_only=True)

    class Meta:
        model = Payout
        fields = [
            "id",
            "bank_account",
            "amount_paise",
            "status",
            "attempt_count",
            "failure_code",
            "failure_reason",
            "created_at",
            "updated_at",
            "completed_at",
            "next_retry_at",
        ]


class PayoutCreateSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.UUIDField()
