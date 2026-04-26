from uuid import UUID

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotAuthenticated, ParseError, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BankAccount, IdempotencyKey, LedgerEntry, Merchant, Payout
from .serializers import BankAccountSerializer, LedgerEntrySerializer, MerchantSerializer, PayoutCreateSerializer, PayoutSerializer
from .services import (
    IdempotencyConflict,
    create_payout_with_idempotency,
    ledger_balances_for_merchant,
)


class MerchantScopedAPIView(APIView):
    def get_merchant(self) -> Merchant:
        merchant_id = self.request.headers.get("X-Merchant-Id")
        if not merchant_id:
            raise NotAuthenticated("X-Merchant-Id header is required.")
        return get_object_or_404(Merchant, pk=merchant_id)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "ok"})


class MerchantListView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        merchants = Merchant.objects.all()
        return Response(MerchantSerializer(merchants, many=True).data)


class BankAccountListView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        accounts = BankAccount.objects.filter(merchant=merchant, is_active=True)
        return Response(BankAccountSerializer(accounts, many=True).data)


class BalanceSummaryView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        balances = ledger_balances_for_merchant(merchant.id)
        recent_entries = LedgerEntry.objects.filter(merchant=merchant).order_by("-created_at")[:20]
        return Response(
            {
                **balances,
                "materialized_available_balance_paise": merchant.available_balance_paise,
                "materialized_held_balance_paise": merchant.held_balance_paise,
                "recent_ledger_entries": LedgerEntrySerializer(recent_entries, many=True).data,
            }
        )


class PayoutListCreateView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        payouts = Payout.objects.filter(merchant=merchant).select_related("bank_account")[:50]
        return Response(PayoutSerializer(payouts, many=True).data)

    def post(self, request):
        merchant = self.get_merchant()
        idempotency_key = self._idempotency_key()
        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = {
            "amount_paise": serializer.validated_data["amount_paise"],
            "bank_account_id": str(serializer.validated_data["bank_account_id"]),
        }
        try:
            body, status_code, replayed = create_payout_with_idempotency(
                merchant_id=merchant.id,
                amount_paise=serializer.validated_data["amount_paise"],
                bank_account_id=serializer.validated_data["bank_account_id"],
                idempotency_key=idempotency_key,
                request_payload=payload,
            )
        except IdempotencyConflict as exc:
            raise ValidationError({"idempotency_key": str(exc)}) from exc
        response = Response(body, status=status_code)
        if replayed:
            response["X-Idempotent-Replay"] = "true"
        return response

    def _idempotency_key(self) -> UUID:
        raw_key = self.request.headers.get("Idempotency-Key")
        if not raw_key:
            raise ParseError("Idempotency-Key header is required.")
        try:
            return UUID(raw_key)
        except ValueError as exc:
            raise ParseError("Idempotency-Key must be a UUID.") from exc


class IdempotencyKeyListView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        keys = IdempotencyKey.objects.filter(merchant=merchant).order_by("-created_at")[:20]
        return Response(
            [
                {
                    "key": str(key.key),
                    "request_hash": key.request_hash,
                    "status_code": key.status_code,
                    "payout_id": str(key.payout_id) if key.payout_id else None,
                    "expires_at": key.expires_at,
                    "created_at": key.created_at,
                }
                for key in keys
            ]
        )
