[EXPLAINER.md] in the repo. Answer these short and specific. This is where most candidates get filtered out.

1. The Ledger. Paste your balance calculation query. Why did you model credits and debits this way?
2. The Lock. Paste the exact code that prevents two concurrent payouts from overdrawing a balance. Explain what database primitive it relies on.
3. The Idempotency. How does your system know it has seen a key before? What happens if the first request is in flight when the second arrives?
4. The State Machine. Where in the code is failed-to-completed blocked? Show the check.
5. The AI Audit. One specific example where AI wrote subtly wrong code (bad locking, wrong aggregation, race condition). Paste what it gave you, what you caught, and what you replaced it with.

Optional bonuses: docker-compose.yml, event sourcing, webhook delivery with retries, audit log. Do not do all of these, just the ones you care about.

The [EXPLAINER.md] section is where we find out whether you actually understand what you shipped.

