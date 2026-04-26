# Gotchas

- Do not catch broad `IntegrityError` around money-moving workflows and assume it means one expected race. Check structured database metadata such as SQLSTATE and the exact constraint before switching to a recovery path.
- Do not run ledger-wide aggregate queries inside high-contention row locks. Use the locked materialized balance on the hot path and keep aggregates for audits or anomaly recovery.
- Do not trust materialized balances just because they are high enough. Stale-high balances can overdraw the ledger, so prove the materialized snapshot is current before creating a payout.
- Do not expose impossible "in progress" API states for idempotency when the database unique index and transaction boundary make callers wait for a committed response.
- Do not let materialized balance drift strand terminal payout transitions. Reconcile from the ledger under lock before giving up on releasing held funds.
- Do not use Celery `autoretry_for=(Exception,)` around business state machines. Business retries belong in the domain model, not in a catch-all task wrapper.
