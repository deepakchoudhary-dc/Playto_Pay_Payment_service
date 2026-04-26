from django.urls import path

from .views import (
    BalanceSummaryView,
    BankAccountListView,
    HealthView,
    IdempotencyKeyListView,
    MerchantListView,
    PayoutListCreateView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("merchants/", MerchantListView.as_view(), name="merchant-list"),
    path("bank-accounts/", BankAccountListView.as_view(), name="bank-account-list"),
    path("ledger/summary/", BalanceSummaryView.as_view(), name="balance-summary"),
    path("payouts/", PayoutListCreateView.as_view(), name="payout-list-create"),
    path("idempotency-keys/", IdempotencyKeyListView.as_view(), name="idempotency-key-list"),
]
