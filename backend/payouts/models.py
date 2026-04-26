import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class InvalidPayoutTransition(ValueError):
    pass


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    email = models.EmailField(unique=True)
    available_balance_paise = models.BigIntegerField(default=0)
    held_balance_paise = models.BigIntegerField(default=0)
    reconciled_available_balance_paise = models.BigIntegerField(default=0)
    reconciled_held_balance_paise = models.BigIntegerField(default=0)
    balance_reconciled_ledger_entry_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, related_name="bank_accounts", on_delete=models.CASCADE)
    beneficiary_name = models.CharField(max_length=160)
    bank_name = models.CharField(max_length=160)
    masked_account_number = models.CharField(max_length=32)
    ifsc = models.CharField(max_length=16)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["bank_name", "masked_account_number"]

    def __str__(self) -> str:
        return f"{self.bank_name} {self.masked_account_number}"


class Payout(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.PROCESSING},
        Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
        Status.COMPLETED: set(),
        Status.FAILED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, related_name="payouts", on_delete=models.PROTECT)
    bank_account = models.ForeignKey(BankAccount, related_name="payouts", on_delete=models.PROTECT)
    amount_paise = models.BigIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["merchant", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount_paise__gt=0), name="payout_amount_positive"),
        ]

    def transition_to(self, next_status: str) -> None:
        allowed = self.ALLOWED_TRANSITIONS[self.status]
        if next_status not in allowed:
            raise InvalidPayoutTransition(f"Illegal payout transition {self.status} -> {next_status}")
        self.status = next_status


class LedgerEntry(models.Model):
    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Bucket(models.TextChoices):
        AVAILABLE = "available", "Available"
        HELD = "held", "Held"

    class Reason(models.TextChoices):
        CUSTOMER_PAYMENT = "customer_payment", "Customer payment"
        PAYOUT_HOLD = "payout_hold", "Payout hold"
        PAYOUT_SETTLEMENT = "payout_settlement", "Payout settlement"
        PAYOUT_RELEASE = "payout_release", "Payout release"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, related_name="ledger_entries", on_delete=models.PROTECT)
    payout = models.ForeignKey(Payout, related_name="ledger_entries", null=True, blank=True, on_delete=models.PROTECT)
    amount_paise = models.BigIntegerField(validators=[MinValueValidator(1)])
    direction = models.CharField(max_length=8, choices=Direction.choices)
    bucket = models.CharField(max_length=16, choices=Bucket.choices)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["merchant", "bucket", "direction"]),
            models.Index(fields=["merchant", "-created_at"]),
            models.Index(fields=["payout"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount_paise__gt=0), name="ledger_amount_positive"),
        ]

    def clean(self) -> None:
        if self.payout_id and self.payout.merchant_id != self.merchant_id:
            raise ValidationError("Ledger entry merchant must match payout merchant.")


class IdempotencyKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, related_name="idempotency_keys", on_delete=models.CASCADE)
    key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    response_body = models.JSONField(null=True, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    payout = models.ForeignKey(Payout, null=True, blank=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["merchant", "key"], name="unique_idempotency_key_per_merchant"),
        ]
        indexes = [
            models.Index(fields=["merchant", "key"]),
            models.Index(fields=["expires_at"]),
        ]
