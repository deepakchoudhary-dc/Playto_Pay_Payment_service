import hashlib
import json
import random
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import BigIntegerField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import BankAccount, IdempotencyKey, LedgerEntry, Merchant, Payout

IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_UNIQUE_CONSTRAINT = "unique_idempotency_key_per_merchant"
UNIQUE_VIOLATION_SQLSTATE = "23505"
MAX_PAYOUT_ATTEMPTS = 3
BASE_RETRY_SECONDS = 30


class IdempotencyConflict(ValueError):
    pass


class BalanceInvariantError(RuntimeError):
    pass


def hash_request_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sum_amount(filter_q: Q):
    return Coalesce(
        Sum("amount_paise", filter=filter_q),
        Value(0),
        output_field=BigIntegerField(),
    )


def ledger_balances_for_merchant(merchant_id: UUID | str) -> dict[str, int]:
    totals = LedgerEntry.objects.filter(merchant_id=merchant_id).aggregate(
        available_credits=_sum_amount(
            Q(bucket=LedgerEntry.Bucket.AVAILABLE, direction=LedgerEntry.Direction.CREDIT)
        ),
        available_debits=_sum_amount(
            Q(bucket=LedgerEntry.Bucket.AVAILABLE, direction=LedgerEntry.Direction.DEBIT)
        ),
        held_credits=_sum_amount(Q(bucket=LedgerEntry.Bucket.HELD, direction=LedgerEntry.Direction.CREDIT)),
        held_debits=_sum_amount(Q(bucket=LedgerEntry.Bucket.HELD, direction=LedgerEntry.Direction.DEBIT)),
    )
    available = totals["available_credits"] - totals["available_debits"]
    held = totals["held_credits"] - totals["held_debits"]
    return {
        "available_balance_paise": available,
        "held_balance_paise": held,
        "total_balance_paise": available + held,
    }


def reconcile_materialized_balances(merchant_id: UUID | str) -> dict[str, int]:
    balances = ledger_balances_for_merchant(merchant_id)
    latest_ledger_entry_id = _latest_ledger_entry_id(merchant_id)
    Merchant.objects.filter(pk=merchant_id).update(
        available_balance_paise=balances["available_balance_paise"],
        held_balance_paise=balances["held_balance_paise"],
        reconciled_available_balance_paise=balances["available_balance_paise"],
        reconciled_held_balance_paise=balances["held_balance_paise"],
        balance_reconciled_ledger_entry_id=latest_ledger_entry_id,
        updated_at=timezone.now(),
    )
    return balances


def payout_response(payout: Payout) -> dict:
    return {
        "id": str(payout.id),
        "merchant_id": str(payout.merchant_id),
        "bank_account_id": str(payout.bank_account_id),
        "amount_paise": payout.amount_paise,
        "status": payout.status,
        "attempt_count": payout.attempt_count,
        "failure_code": payout.failure_code,
        "failure_reason": payout.failure_reason,
        "created_at": payout.created_at.isoformat(),
        "updated_at": payout.updated_at.isoformat(),
        "completed_at": payout.completed_at.isoformat() if payout.completed_at else None,
        "next_retry_at": payout.next_retry_at.isoformat() if payout.next_retry_at else None,
    }


def create_payout_with_idempotency(
    *,
    merchant_id: UUID | str,
    amount_paise: int,
    bank_account_id: UUID | str,
    idempotency_key: UUID,
    request_payload: dict,
) -> tuple[dict, int, bool]:
    request_hash = hash_request_payload(request_payload)
    try:
        return _create_payout_with_idempotency_tx(
            merchant_id=merchant_id,
            amount_paise=amount_paise,
            bank_account_id=bank_account_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except IntegrityError as exc:
        if not _is_idempotency_key_collision(exc):
            raise
        return _return_committed_idempotency_response(
            merchant_id=merchant_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )


def _is_idempotency_key_collision(exc: IntegrityError) -> bool:
    return (
        _database_error_sqlstate(exc) == UNIQUE_VIOLATION_SQLSTATE
        and _database_error_constraint_name(exc) == IDEMPOTENCY_UNIQUE_CONSTRAINT
    )


def _iter_database_errors(exc: BaseException):
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _database_error_sqlstate(exc: BaseException) -> str | None:
    for error in _iter_database_errors(exc):
        sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
        if sqlstate:
            return sqlstate
        diag = getattr(error, "diag", None)
        sqlstate = getattr(diag, "sqlstate", None) or getattr(diag, "returned_sqlstate", None)
        if sqlstate:
            return sqlstate
    return None


def _database_error_constraint_name(exc: BaseException) -> str | None:
    for error in _iter_database_errors(exc):
        diag = getattr(error, "diag", None)
        constraint_name = getattr(diag, "constraint_name", None)
        if constraint_name:
            return constraint_name
    return None


@transaction.atomic
def _create_payout_with_idempotency_tx(
    *,
    merchant_id: UUID | str,
    amount_paise: int,
    bank_account_id: UUID | str,
    idempotency_key: UUID,
    request_hash: str,
) -> tuple[dict, int, bool]:
    now = timezone.now()
    record = (
        IdempotencyKey.objects.select_for_update()
        .filter(merchant_id=merchant_id, key=idempotency_key)
        .first()
    )

    if record and record.expires_at > now:
        _assert_same_request(record, request_hash)
        return _stored_idempotency_response(record)

    if record and record.expires_at <= now:
        record.request_hash = request_hash
        record.response_body = None
        record.status_code = None
        record.payout = None
        record.expires_at = now + IDEMPOTENCY_TTL
        record.save(update_fields=["request_hash", "response_body", "status_code", "payout", "expires_at", "updated_at"])
    else:
        record = IdempotencyKey.objects.create(
            merchant_id=merchant_id,
            key=idempotency_key,
            request_hash=request_hash,
            expires_at=now + IDEMPOTENCY_TTL,
        )

    response_body, status_code, payout = _create_payout_after_idempotency_record(
        merchant_id=merchant_id,
        amount_paise=amount_paise,
        bank_account_id=bank_account_id,
    )
    record.response_body = response_body
    record.status_code = status_code
    record.payout = payout
    record.save(update_fields=["response_body", "status_code", "payout", "updated_at"])
    return response_body, status_code, False


@transaction.atomic
def _return_committed_idempotency_response(
    *,
    merchant_id: UUID | str,
    idempotency_key: UUID,
    request_hash: str,
) -> tuple[dict, int, bool]:
    record = (
        IdempotencyKey.objects.select_for_update()
        .filter(merchant_id=merchant_id, key=idempotency_key)
        .first()
    )
    if record is None:
        raise IntegrityError("Idempotency insert failed, but the committed key was not found.")
    _assert_same_request(record, request_hash)
    return _stored_idempotency_response(record)


def _assert_same_request(record: IdempotencyKey, request_hash: str) -> None:
    if record.request_hash != request_hash:
        raise IdempotencyConflict("Idempotency-Key has already been used with a different request body.")


def _stored_idempotency_response(record: IdempotencyKey) -> tuple[dict, int, bool]:
    if record.response_body is None or record.status_code is None:
        raise RuntimeError("Committed idempotency key has no stored response.")
    return record.response_body, record.status_code, True


def _create_payout_after_idempotency_record(
    *,
    merchant_id: UUID | str,
    amount_paise: int,
    bank_account_id: UUID | str,
) -> tuple[dict, int, Payout | None]:
    if amount_paise <= 0:
        return {"detail": "amount_paise must be greater than 0."}, 400, None

    merchant = Merchant.objects.select_for_update().get(pk=merchant_id)
    bank_account = BankAccount.objects.filter(pk=bank_account_id, merchant=merchant, is_active=True).first()
    if bank_account is None:
        return {"detail": "Active bank account was not found for this merchant."}, 400, None

    if _materialized_balance_needs_repair(merchant):
        _reconcile_locked_merchant_balances(merchant)

    if merchant.available_balance_paise < amount_paise:
        return {
            "detail": "Insufficient available balance.",
            "available_balance_paise": merchant.available_balance_paise,
        }, 400, None

    now = timezone.now()
    updated = Merchant.objects.filter(pk=merchant.pk, available_balance_paise__gte=amount_paise).update(
        available_balance_paise=F("available_balance_paise") - amount_paise,
        held_balance_paise=F("held_balance_paise") + amount_paise,
        reconciled_available_balance_paise=F("available_balance_paise") - amount_paise,
        reconciled_held_balance_paise=F("held_balance_paise") + amount_paise,
        updated_at=now,
    )
    if updated != 1:
        balances = _reconcile_locked_merchant_balances(merchant)
        if balances["available_balance_paise"] < amount_paise:
            return {
                "detail": "Insufficient available balance.",
                "available_balance_paise": balances["available_balance_paise"],
            }, 400, None

        updated = Merchant.objects.filter(pk=merchant.pk, available_balance_paise__gte=amount_paise).update(
            available_balance_paise=F("available_balance_paise") - amount_paise,
            held_balance_paise=F("held_balance_paise") + amount_paise,
            reconciled_available_balance_paise=F("available_balance_paise") - amount_paise,
            reconciled_held_balance_paise=F("held_balance_paise") + amount_paise,
            updated_at=timezone.now(),
        )

    if updated != 1:
        return {
            "detail": "Balance changed while creating the payout. Retry with a new key.",
            "available_balance_paise": merchant.available_balance_paise,
        }, 409, None

    payout = Payout.objects.create(
        merchant=merchant,
        bank_account=bank_account,
        amount_paise=amount_paise,
        status=Payout.Status.PENDING,
    )
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                merchant=merchant,
                payout=payout,
                amount_paise=amount_paise,
                direction=LedgerEntry.Direction.DEBIT,
                bucket=LedgerEntry.Bucket.AVAILABLE,
                reason=LedgerEntry.Reason.PAYOUT_HOLD,
                description=f"Funds held for payout {payout.id}",
            ),
            LedgerEntry(
                merchant=merchant,
                payout=payout,
                amount_paise=amount_paise,
                direction=LedgerEntry.Direction.CREDIT,
                bucket=LedgerEntry.Bucket.HELD,
                reason=LedgerEntry.Reason.PAYOUT_HOLD,
                description=f"Funds held for payout {payout.id}",
            ),
        ]
    )
    _mark_merchant_reconciled_to_latest_ledger(merchant.id)
    return payout_response(payout), 201, payout


def _reconcile_locked_merchant_balances(merchant: Merchant) -> dict[str, int]:
    balances = ledger_balances_for_merchant(merchant.id)
    latest_ledger_entry_id = _latest_ledger_entry_id(merchant.id)
    merchant.available_balance_paise = balances["available_balance_paise"]
    merchant.held_balance_paise = balances["held_balance_paise"]
    merchant.reconciled_available_balance_paise = balances["available_balance_paise"]
    merchant.reconciled_held_balance_paise = balances["held_balance_paise"]
    merchant.balance_reconciled_ledger_entry_id = latest_ledger_entry_id
    merchant.save(
        update_fields=[
            "available_balance_paise",
            "held_balance_paise",
            "reconciled_available_balance_paise",
            "reconciled_held_balance_paise",
            "balance_reconciled_ledger_entry_id",
            "updated_at",
        ]
    )
    return balances


def _materialized_balance_needs_repair(merchant: Merchant) -> bool:
    if merchant.available_balance_paise != merchant.reconciled_available_balance_paise:
        return True
    if merchant.held_balance_paise != merchant.reconciled_held_balance_paise:
        return True
    return merchant.balance_reconciled_ledger_entry_id != _latest_ledger_entry_id(merchant.id)


def _latest_ledger_entry_id(merchant_id: UUID | str) -> UUID | None:
    return (
        LedgerEntry.objects.filter(merchant_id=merchant_id)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
        .first()
    )


def _mark_merchant_reconciled_to_latest_ledger(merchant_id: UUID | str) -> None:
    Merchant.objects.filter(pk=merchant_id).update(
        balance_reconciled_ledger_entry_id=_latest_ledger_entry_id(merchant_id),
        updated_at=timezone.now(),
    )


@transaction.atomic
def start_payout_attempt(payout_id: UUID | str) -> dict:
    now = timezone.now()
    payout = Payout.objects.select_for_update().get(pk=payout_id)
    if payout.status == Payout.Status.PENDING:
        payout.transition_to(Payout.Status.PROCESSING)
    elif payout.status == Payout.Status.PROCESSING:
        if payout.next_retry_at and payout.next_retry_at > now:
            return {"started": False, "reason": "retry_not_due", "payout_id": str(payout.id)}
    else:
        return {"started": False, "reason": f"terminal_{payout.status}", "payout_id": str(payout.id)}

    if payout.attempt_count >= MAX_PAYOUT_ATTEMPTS:
        return {"started": False, "reason": "max_attempts_exceeded", "payout_id": str(payout.id)}

    payout.attempt_count += 1
    payout.last_attempt_at = now
    payout.next_retry_at = now + timedelta(seconds=BASE_RETRY_SECONDS * (2 ** (payout.attempt_count - 1)))
    payout.save(
        update_fields=[
            "status",
            "attempt_count",
            "last_attempt_at",
            "next_retry_at",
            "updated_at",
        ]
    )
    return {"started": True, "attempt_count": payout.attempt_count, "payout_id": str(payout.id)}


def choose_bank_outcome() -> str:
    roll = random.random()
    if roll < 0.70:
        return "success"
    if roll < 0.90:
        return "failure"
    return "hang"


def process_payout_once(payout_id: UUID | str, forced_outcome: str | None = None) -> dict:
    attempt = start_payout_attempt(payout_id)
    if not attempt["started"]:
        if attempt["reason"] == "max_attempts_exceeded":
            return fail_payout(
                payout_id,
                failure_code="max_attempts",
                failure_reason="Payout exceeded the maximum retry attempts.",
            )
        return attempt

    outcome = forced_outcome or choose_bank_outcome()
    if outcome == "success":
        return complete_payout(payout_id)
    if outcome == "failure":
        return fail_payout(
            payout_id,
            failure_code="bank_rejected",
            failure_reason="Simulated bank settlement failed.",
        )
    if outcome == "hang":
        payout = Payout.objects.get(pk=payout_id)
        return payout_response(payout)
    raise ValueError(f"Unknown payout outcome {outcome}")


@transaction.atomic
def complete_payout(payout_id: UUID | str) -> dict:
    payout = Payout.objects.select_for_update().get(pk=payout_id)
    payout.transition_to(Payout.Status.COMPLETED)

    _settle_held_funds(payout)

    LedgerEntry.objects.create(
        merchant_id=payout.merchant_id,
        payout=payout,
        amount_paise=payout.amount_paise,
        direction=LedgerEntry.Direction.DEBIT,
        bucket=LedgerEntry.Bucket.HELD,
        reason=LedgerEntry.Reason.PAYOUT_SETTLEMENT,
        description=f"Payout {payout.id} completed",
    )
    _mark_merchant_reconciled_to_latest_ledger(payout.merchant_id)
    payout.completed_at = timezone.now()
    payout.next_retry_at = None
    payout.save(update_fields=["status", "completed_at", "next_retry_at", "updated_at"])
    return payout_response(payout)


@transaction.atomic
def fail_payout(payout_id: UUID | str, *, failure_code: str, failure_reason: str) -> dict:
    payout = Payout.objects.select_for_update().get(pk=payout_id)
    payout.transition_to(Payout.Status.FAILED)

    _release_held_funds(payout)

    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                merchant_id=payout.merchant_id,
                payout=payout,
                amount_paise=payout.amount_paise,
                direction=LedgerEntry.Direction.DEBIT,
                bucket=LedgerEntry.Bucket.HELD,
                reason=LedgerEntry.Reason.PAYOUT_RELEASE,
                description=f"Payout {payout.id} failed; held funds released",
            ),
            LedgerEntry(
                merchant_id=payout.merchant_id,
                payout=payout,
                amount_paise=payout.amount_paise,
                direction=LedgerEntry.Direction.CREDIT,
                bucket=LedgerEntry.Bucket.AVAILABLE,
                reason=LedgerEntry.Reason.PAYOUT_RELEASE,
                description=f"Payout {payout.id} failed; funds returned to available balance",
            ),
        ]
    )
    _mark_merchant_reconciled_to_latest_ledger(payout.merchant_id)
    payout.failure_code = failure_code
    payout.failure_reason = failure_reason
    payout.completed_at = timezone.now()
    payout.next_retry_at = None
    payout.save(
        update_fields=[
            "status",
            "failure_code",
            "failure_reason",
            "completed_at",
            "next_retry_at",
            "updated_at",
        ]
    )
    return payout_response(payout)


def _settle_held_funds(payout: Payout) -> None:
    merchant = Merchant.objects.select_for_update().get(pk=payout.merchant_id)
    if _materialized_balance_needs_repair(merchant):
        _reconcile_locked_merchant_balances(merchant)

    if merchant.held_balance_paise < payout.amount_paise:
        raise BalanceInvariantError("Payout completion found less ledger-held balance than the payout amount.")

    updated = Merchant.objects.filter(pk=payout.merchant_id, held_balance_paise__gte=payout.amount_paise).update(
        held_balance_paise=F("held_balance_paise") - payout.amount_paise,
        reconciled_available_balance_paise=F("available_balance_paise"),
        reconciled_held_balance_paise=F("held_balance_paise") - payout.amount_paise,
        updated_at=timezone.now(),
    )
    if updated == 1:
        return

    balances = ledger_balances_for_merchant(payout.merchant_id)
    if balances["held_balance_paise"] < payout.amount_paise:
        raise BalanceInvariantError("Payout completion found less ledger-held balance than the payout amount.")

    merchant.available_balance_paise = balances["available_balance_paise"]
    merchant.held_balance_paise = balances["held_balance_paise"] - payout.amount_paise
    merchant.reconciled_available_balance_paise = balances["available_balance_paise"]
    merchant.reconciled_held_balance_paise = balances["held_balance_paise"] - payout.amount_paise
    merchant.balance_reconciled_ledger_entry_id = _latest_ledger_entry_id(payout.merchant_id)
    merchant.save(
        update_fields=[
            "available_balance_paise",
            "held_balance_paise",
            "reconciled_available_balance_paise",
            "reconciled_held_balance_paise",
            "balance_reconciled_ledger_entry_id",
            "updated_at",
        ]
    )


def _release_held_funds(payout: Payout) -> None:
    merchant = Merchant.objects.select_for_update().get(pk=payout.merchant_id)
    if _materialized_balance_needs_repair(merchant):
        _reconcile_locked_merchant_balances(merchant)

    if merchant.held_balance_paise < payout.amount_paise:
        raise BalanceInvariantError("Payout failure found less ledger-held balance than the payout amount.")

    updated = Merchant.objects.filter(pk=payout.merchant_id, held_balance_paise__gte=payout.amount_paise).update(
        available_balance_paise=F("available_balance_paise") + payout.amount_paise,
        held_balance_paise=F("held_balance_paise") - payout.amount_paise,
        reconciled_available_balance_paise=F("available_balance_paise") + payout.amount_paise,
        reconciled_held_balance_paise=F("held_balance_paise") - payout.amount_paise,
        updated_at=timezone.now(),
    )
    if updated == 1:
        return

    balances = ledger_balances_for_merchant(payout.merchant_id)
    if balances["held_balance_paise"] < payout.amount_paise:
        raise BalanceInvariantError("Payout failure found less ledger-held balance than the payout amount.")

    merchant.available_balance_paise = balances["available_balance_paise"] + payout.amount_paise
    merchant.held_balance_paise = balances["held_balance_paise"] - payout.amount_paise
    merchant.reconciled_available_balance_paise = balances["available_balance_paise"] + payout.amount_paise
    merchant.reconciled_held_balance_paise = balances["held_balance_paise"] - payout.amount_paise
    merchant.balance_reconciled_ledger_entry_id = _latest_ledger_entry_id(payout.merchant_id)
    merchant.save(
        update_fields=[
            "available_balance_paise",
            "held_balance_paise",
            "reconciled_available_balance_paise",
            "reconciled_held_balance_paise",
            "balance_reconciled_ledger_entry_id",
            "updated_at",
        ]
    )


def due_processing_payout_ids(limit: int = 50) -> list[str]:
    now = timezone.now()
    ids = (
        Payout.objects.filter(status=Payout.Status.PROCESSING, next_retry_at__lte=now)
        .order_by("next_retry_at")
        .values_list("id", flat=True)[:limit]
    )
    return [str(payout_id) for payout_id in ids]


def pending_payout_ids(limit: int = 50) -> list[str]:
    ids = (
        Payout.objects.filter(status=Payout.Status.PENDING)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    return [str(payout_id) for payout_id in ids]
