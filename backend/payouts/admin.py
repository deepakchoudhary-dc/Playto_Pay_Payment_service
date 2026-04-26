from django.contrib import admin

from .models import BankAccount, IdempotencyKey, LedgerEntry, Merchant, Payout


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "available_balance_paise", "held_balance_paise", "created_at"]
    search_fields = ["name", "email"]


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ["merchant", "bank_name", "masked_account_number", "ifsc", "is_active"]
    list_filter = ["is_active", "bank_name"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "amount_paise", "status", "attempt_count", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["id", "merchant__name", "merchant__email"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["id", "merchant", "amount_paise", "direction", "bucket", "reason", "created_at"]
    list_filter = ["direction", "bucket", "reason"]
    search_fields = ["id", "merchant__name", "payout__id"]


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ["key", "merchant", "status_code", "payout", "expires_at", "created_at"]
    search_fields = ["key", "merchant__name", "payout__id"]
