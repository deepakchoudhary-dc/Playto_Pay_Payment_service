# Playto Pay Payout Engine

Minimal payout engine for Indian merchants collecting international payments. The backend is Django + DRF with PostgreSQL and Celery. The dashboard is React + Tailwind.

## Run with Docker

```bash
docker compose up --build
```

The backend runs at `http://localhost:8000/api/v1/` and the dashboard runs at `http://localhost:5173/`.

The backend container runs migrations and seeds three merchants with credit ledger history. Celery worker and beat process pending payouts and retries.

## Local Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_playto
python manage.py runserver
```

Run the worker and scheduler in separate shells:

```bash
celery -A playtopay worker --loglevel=info
celery -A playtopay beat --loglevel=info
```

## Local Frontend

```bash
cd frontend
npm install
npm run dev
```

## API Shape

All merchant-scoped endpoints use `X-Merchant-Id`.

`GET /api/v1/merchants/` lists seeded merchants.

`GET /api/v1/ledger/summary/` returns available, held, total, and recent ledger entries.

`GET /api/v1/bank-accounts/` returns active payout accounts.

`GET /api/v1/payouts/` returns recent payouts.

`POST /api/v1/payouts/` creates a payout hold. Required header: `Idempotency-Key: <uuid>`. Body:

```json
{
  "amount_paise": 6000,
  "bank_account_id": "..."
}
```

## Verification

```bash
cd backend
python manage.py test payouts
```

The concurrency test is skipped unless the test database supports `SELECT ... FOR UPDATE`; run it against PostgreSQL for the real row-lock behavior.
