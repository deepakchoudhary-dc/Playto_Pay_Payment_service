About Playto Pay: Cross-border payment infrastructure for Indian agencies, freelancers, and online businesses who cannot access Stripe or PayPal. Think of us as the Mercury-equivalent for emerging market businesses. 

While making this project Our main focus is: shiping money-moving code to production also, Architecture decisions matter more than polish. Correctness matters more than features.

## The context

Playto Pay helps Indian agencies and freelancers collect international payments. Money flows in one direction: international customer pays in USD, Playto collects, Playto pays merchant in INR.

The hardest part is not payment collection. It is the payout engine that sits in the middle. Merchants accumulate balance when their customers pay, and they withdraw to their Indian bank account. Your challenge is to build a minimal version of that engine.

## The Playto Payout Engine

Build a service where merchants can see their balance, request payouts, and track payout status. The service must handle the concurrency, idempotency, and data integrity problems that real payment systems fail at.

Stack: Backend is Django plus DRF. Frontend is React plus Tailwind. Database is PostgreSQL, strongly preferred. Background jobs via Celery, Django-Q, or Huey. Do not fake it with sync code.

## Core features

Merchant Ledger. Every merchant has a balance in paise as an integer, never floats. Balance is derived from credits (simulated customer payments) and debits (payouts). Seed 2 to 3 merchants with credit history. You do not need to build the customer payment flow.

Payout Request API. POST to /api/v1/payouts with an idempotency key in the header. Body has amount_paise and bank_account_id. Creates a payout in pending state and holds the funds. Returns the same response if called twice with the same idempotency key.

Payout Processor background worker. Picks up pending payouts and moves them through the lifecycle. Simulate bank settlement: succeed 70 percent, fail 20 percent, hang in processing 10 percent. On success, the payout is final. On failure, the held funds return to the merchant balance.

Merchant Dashboard in React. Shows available balance, held balance, recent credits and debits. Form to request a payout. Table of payout history with live status updates.

## Technical constraints

These are the parts we actually grade you on. Features are easy. These are not.

Money integrity. Amounts stored as BigIntegerField in paise. No FloatField. No DecimalField unless you have a good reason. Balance calculations must use database-level operations, not Python arithmetic on fetched rows. The sum of credits minus debits must always equal the displayed balance. We check this invariant.

Concurrency. A merchant with 100 rupees balance submits two simultaneous 60 rupee payout requests. Exactly one should succeed. The other must be rejected cleanly. Race conditions on check-then-deduct are the most common bug we see.

Idempotency. The Idempotency-Key header is a merchant-supplied UUID. Second call with the same key returns the exact same response as the first. No duplicate payout created. Keys scoped per merchant. Keys expire after 24 hours.

State machine. Legal: pending to processing to completed, OR pending to processing to failed. Illegal (must be rejected): completed to pending, failed to completed, anything backwards. A failed payout returning funds must do so atomically with the state transition.

Retry logic. Payouts stuck in processing for more than 30 seconds should be retried. Exponential backoff, max 3 attempts, then move to failed and return funds.

## How we evaluate

Clean ledger model tells us you think like someone who will own a money-moving system.

Correct concurrency handling tells us you know the difference between Python-level and database-level locking.

Good idempotency implementation tells us you have shipped an API that deals with real networks.

Sharp [EXPLAINER.md]tells us you understand your own code and will not freeze in a debugging call.

Honest AI audit tells us you are senior enough to not trust the machine blindly.

We are NOT grading on: pixel-perfect UI, perfect test coverage, fancy patterns, feature completeness beyond what is listed.