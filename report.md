# The Mythos Playbook: Playto Payout Engine - Ultimate System Design Handout
**Reviewer:** "Mythos"
**Date:** April 2026

This is the fully comprehensive, fail-proof architecture review of the Playto Payout Engine. I have torn your code apart through multiple stages of humiliating failures until you finally stabilized it. Use this document as your definitive "cheat code" for any system design interview related to money-moving systems, concurrency, idling connections, and distributed ledger architecture.

If you read this, understand it, and can recite its core teachings, no interviewer will be able to break you.

---

## 1. Data Integrity: The Basics

*If you get this wrong, money disappears, and you go to jail. Simple as that.*

**Why BigIntegerField tracking "Paise"?**
*   **The Mistake:** Using `FloatField` or `DecimalField` in PostgreSQL. Floats are mathematically imprecise and succumb to IEEE 754 precision rounding errors.
*   **The Fix:** Treat money as discrete integer representations of the smallest denominator (Paise for INR, Cents for USD). `BigIntegerField` cleanly scales into the trillions of dollars without overflowing standard 32-bit `Int`.

**Why Double-Entry ledgers?**
*   **The Mistake:** Merely tracking an `available_balance = 100` column without an immutable history log indicating *why* that balance exists.
*   **The Fix:** A pure append-only `LedgerEntry` table tracking `Bucket` (Available vs Held) and `Direction` (Credit vs Debit). The immutable constraint: The sum total of a merchant's credits minus debits must *always* equal their materialized balance cache.

---

## 2. Concurrency & The Split-Brain Paradox

*This is where 99% of "Senior" Engineers fail the interview.*

**The Race Condition (Check-then-Deduct):**
*   **The Mistake:** Python fetches `balance = 100`. Python sees `100 >= 60`. Python updates `balance = 100 - 60`. If two distinct API requests run this concurrently, both read `100`, both deduct `60`, and the database records `40` while `120` was actually withdrawn. The merchant overdraws.
*   **The Postgres Row Lock (`SELECT ... FOR UPDATE`):**
    *   Tying the operations inside an atomic transaction block and fetching `Merchant.objects.select_for_update()`.
    *   The second request halts execution at the Postgres disk-level locking phase and waits. Only when Transaction A commits or rolls back does Transaction B fetch the newly updated row data.

**The O(N) Chokehold vs. The Split-Brain Death Trap:**
*   **The O(N) Mistake:** Running an aggregate query `Sum()` over all historical `LedgerEntry` records while holding the row-lock. This grinds the system to a halt for massive merchants because row-locks artificially bottleneck while table-spanning aggregations compute real-time.
*   **The Split-Brain Mistake:** Checking the fast materialized `F('available_balance_paise')` cache, but if it accidentally drifts off the mathematical `Sum()` (due to bugs, manual schema edits), throwing an unrecoverable 500 error or permanent 409 conflict, bricking the user account.
*   **The Mythos "Lazy Read-Repair" Fix:**
    *   Optimistically aim for an `O(1)` atomic query `UPDATE Merchant ... WHERE available_balance >= amount`. 
    *   If that `UPDATE` fails to touch exactly 1 row (meaning the materialized cash implies an overdraft), **only then** do you trigger the heavy `O(N)` ledger aggregation.
    *   If the aggregate proves there genuinely *is* sufficient balance historically, automatically override and repair the materialized values, then continue the transaction dynamically. 
    *   *Conclusion:* You achieve massive performance by skipping O(N) queries on 99.9% of requests, but you auto-heal the data layer on the 0.1% of split-brain anomalies without waking an engineer up.

---

## 3. Idempotency: Isolation & The ACID Paradox

*Networks fail. Clients retry. You must never pay someone twice.*

**The Payload Hashes:**
*   **The Standard:** The client provides an `Idempotency-Key` (a UUID scoped per merchant) in headers.
*   **The Fix:** You also must hash the exact request payload using SHA-256 and store it. If the client sends the *same* Idempotency-Key but suddenly changes the withdrawal amount from `$`10 to `$`10,000, you throw an `IdempotencyConflict`.

**The Concurrency Lock on Idempotency:**
*   **The Mistake:** Using `filter().first()`. If it doesn't exist, create it. Under concurrent bursts, two requests run `filter()`, both see nothing, both attempt to `insert()`, and one crashes on the `UNIQUE` DB constraint.
*   **The Mathematical Paradox Mistake:** Catching `IntegrityError` and then trying to write an empty `response_body` check. Because of Postgres's `Read Committed` isolation level, the second transaction is frozen and physically *cannot* read the row until the first transaction fully completes and populates the response body anyway.
*   **The Mythos Fix:** 
    *   Run `create_payout_with_idempotency_tx` inside a `@transaction.atomic` block. 
    *   Catch `IntegrityError`. 
    *   Dynamically parse the exact Database drivers (`sqlstate == 23505`) using the `psycopg2` driver cause chain instead of brittle string mapping.
    *   Fall back to `_return_committed_idempotency_response()` to effortlessly retrieve the results the first conflicting transaction successfully yielded.

---

## 4. State Machines & Asynchronous Resiliency

*Your system will fail. The difference between an amateur and a pro is what happens after the crash.*

**The "Held" Funds Pattern:**
*   **The Rule:** You do not just deduct funds. You deduct `available` and credit `held`. Total liability doesn't change until the bank clears the transaction. 
*   **Irreversible Transitions:** A Payout can move `Pending` -> `Processing`. From there, it goes `Completed` or `Failed`. It can **never** revert. State transition matrix dictates that if a user tries to fail a completed payout, the API rejects it synchronously.

**Decoupling Celery/Infrastructure from Business Logic:**
*   **The Mistake:** Depending on Celery's `autoretry_for=(Exception,)` feature to retry stuck processing logic. This causes infrastructure queues to override your own domain logic resulting in rapid-fire retry exhaustions masking actual 500 errors.
*   **The Fix:**
    *   Persist the `next_retry_at` column directly natively into the database context.
    *   Configure a lightweight `Celery Beat` scheduler to `SELECT` and sweep any `Processing` payouts whose `next_retry_at` timestamp is historically past due.
    *   The business logic maintains ultimate authoritative control over exponential backoff calculation independent of whatever broker (`Redis/RabbitMQ`) you use.

---

### Interview Q&A Cheatsheet: If they grill you on this system

**Q: What if the database loses connection holding a row-lock?** 
*A: Standard Postgres transaction boundaries safely release row-locks if the TCP connection drops or errors out, preventing permanently deadlocked ledger rows.*

**Q: Why not `REPEATABLE READ` or `SERIALIZABLE` isolation for everything?**
*A: It forces your application to constantly face serialization anomalies and dramatically reduces throughput. Using default `READ COMMITTED` combined with explicit `SELECT ... FOR UPDATE` row locks achieves the needed guarantees locally exactly where it matters with minimal generic query overhead.*

**Q: What if the `psycopg2` error formats change in an upgrade and mask your Idempotency fallback?**
*A: I designed an explicit generator `_iter_database_errors()` the parses the causal exception chain looking specifically for the raw PG SQL code `23505` (`unique_violation`). It completely bypasses Django abstraction layers making it fundamentally immune to basic ORM or driver translation changes.*