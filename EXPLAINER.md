# EXPLAINER.md

## 1. The Ledger

Balance calculation lives in `backend/payouts/services.py`:

```python
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
```

Credits and debits are bucketed into `available` and `held`. A payout request debits available and credits held, so total merchant liability does not change when funds are only reserved. A completed payout debits held. A failed payout debits held and credits available in the same transaction.

`Merchant.available_balance_paise` and `Merchant.held_balance_paise` are materialized copies updated in the same database transactions as the ledger entries. The aggregate query above is used for audits, dashboard reconciliation, and anomaly repair.

The merchant row also stores `reconciled_available_balance_paise`, `reconciled_held_balance_paise`, and `balance_reconciled_ledger_entry_id`. Those fields are a watermark proving which ledger state the materialized balances are known to match. The normal payout admission path avoids full ledger aggregation when the watermark is current; it reconciles from the ledger when the materialized values or ledger watermark do not match.

## 2. The Lock

The overdraft prevention is in `backend/payouts/services.py`:

```python
merchant = Merchant.objects.select_for_update().get(pk=merchant_id)
if _materialized_balance_needs_repair(merchant):
    _reconcile_locked_merchant_balances(merchant)

if merchant.available_balance_paise < amount_paise:
    return {"detail": "Insufficient available balance.", ...}, 400, None

updated = Merchant.objects.filter(pk=merchant.pk, available_balance_paise__gte=amount_paise).update(
    available_balance_paise=F("available_balance_paise") - amount_paise,
    held_balance_paise=F("held_balance_paise") + amount_paise,
    reconciled_available_balance_paise=F("available_balance_paise") - amount_paise,
    reconciled_held_balance_paise=F("held_balance_paise") + amount_paise,
)
```

This relies on PostgreSQL row-level locking from `SELECT ... FOR UPDATE`. The second concurrent request for the same merchant waits for the first transaction to commit, then reads the updated materialized balance. The conditional `UPDATE ... WHERE available_balance_paise >= amount` is a second database-level guard. If the materialized value is stale-low or stale-high, or if ledger entries changed without updating the materialized snapshot, the service repairs from the ledger under the same merchant lock before deciding whether to create or reject the payout.

## 3. The Idempotency

`IdempotencyKey` has a unique constraint on `(merchant, key)`, a SHA-256 request hash, saved response body, saved status code, and `expires_at`.

```python
record = (
    IdempotencyKey.objects.select_for_update()
    .filter(merchant_id=merchant_id, key=idempotency_key)
    .first()
)
```

If the key exists and is unexpired, the service compares the request hash and returns the saved response. If two first calls race, the loser waits on the unique index insert until the winning transaction commits, then catches the specific idempotency unique collision, locks the committed row, and returns the exact saved response:

```python
except IntegrityError as exc:
    if not _is_idempotency_key_collision(exc):
        raise
    return _return_committed_idempotency_response(...)
```

The recovery path only handles SQLSTATE `23505` (`unique_violation`) when the database reports the exact `unique_idempotency_key_per_merchant` constraint. Other integrity failures, such as check constraints, foreign-key errors, or unrelated unique constraints, are not misclassified as idempotency races. Invalid payout amounts are also rejected before `Payout.objects.create()` so direct service calls return a normal 400 response instead of relying on a database check constraint. There is no API-level "idempotency in progress" branch because the transaction and unique index make that state unobservable for committed rows.

Keys are scoped per merchant and reset after 24 hours.

## 4. The State Machine

Illegal transitions are blocked in `backend/payouts/models.py`:

```python
ALLOWED_TRANSITIONS = {
    Status.PENDING: {Status.PROCESSING},
    Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
    Status.COMPLETED: set(),
    Status.FAILED: set(),
}

def transition_to(self, next_status: str) -> None:
    allowed = self.ALLOWED_TRANSITIONS[self.status]
    if next_status not in allowed:
        raise InvalidPayoutTransition(f"Illegal payout transition {self.status} -> {next_status}")
    self.status = next_status
```

`complete_payout()` calls `payout.transition_to(Payout.Status.COMPLETED)` before changing balances. A failed payout has no allowed next states, so failed-to-completed raises before any ledger mutation.

Celery does not autoretry `process_payout` for every exception. The payout service owns business retries through `attempt_count` and `next_retry_at`; the beat task re-enqueues pending and due processing payouts. That keeps infrastructural task failures from consuming business attempts or hiding invariant failures.

## 5. The AI Audit

The first AI-shaped idempotency draft was subtly race-prone:

```python
record = IdempotencyKey.objects.filter(merchant=merchant, key=key).first()
if record:
    return record.response_body, record.status_code
record = IdempotencyKey.objects.create(merchant=merchant, key=key, request_hash=request_hash)
```

That check-then-create shape is unsafe under concurrent first calls with the same key. One request can fail on the unique constraint, and without a recovery path it will not return the first response.

I replaced it with a unique constraint plus explicit row locking and `IntegrityError` recovery:

```python
try:
    return _create_payout_with_idempotency_tx(...)
except IntegrityError as exc:
    if not _is_idempotency_key_collision(exc):
        raise
    return _return_committed_idempotency_response(...)
```

The first replacement was still too broad because `IntegrityError` also covers check constraints. The production-safe version checks structured database error metadata: SQLSTATE `23505` plus the exact idempotency constraint name before replaying a response.

The hot payout path also changed after review: it now trusts the locked materialized merchant balance only when its reconciliation watermark is current. If payout creation sees stale-low or stale-high materialized balance, it reconciles from the ledger once under the lock before creating or rejecting. If a terminal payout transition finds materialized held balance drift, it locks the merchant, reconciles from the ledger once, then applies the completion or failure release. Known-current insufficient balances do not run a full aggregate repeatedly, which avoids the read-repair thundering-herd failure mode while still preventing overdraws from stale-high materialized data.
