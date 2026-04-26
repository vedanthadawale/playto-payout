# Playto Payout Engine

Cross-border payout engine for Indian merchants collecting international payments.  
Money flows: international customer → Playto collects in USD → merchant receives in INR.

**Stack:** Django 4.2 + DRF · PostgreSQL · Celery + Redis · React 18 + Tailwind · Docker

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│  React Dashboard                                            │
│  (balance, payout form, live status table, ledger)          │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST  /api/v1/
┌──────────────────────────▼──────────────────────────────────┐
│  Django + DRF                                               │
│  • GET  /merchants/<id>/         – dashboard data           │
│  • POST /payouts/                – create payout            │
│    [Idempotency-Key header]        [SELECT FOR UPDATE]      │
│  • GET  /payouts/<id>/           – payout detail            │
└──────────────────────────┬──────────────────────────────────┘
                           │ async tasks
┌──────────────────────────▼──────────────────────────────────┐
│  Celery Workers + Beat                                      │
│  • process_single_payout   – PENDING → PROCESSING → result │
│  • retry_stuck_payouts     – recover hangs (every 30s)     │
│  • process_pending_payouts – periodic scheduler (every 10s)│
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  PostgreSQL                                                 │
│  merchants · bank_accounts · ledger_entries                 │
│  payouts · idempotency_keys                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Quickstart — Docker (Recommended)

**Prerequisites:** Docker, Docker Compose

```bash
git clone https://github.com/YOUR_USERNAME/playto-payout.git
cd playto-payout

cp .env.example .env          # edit if needed

docker compose up --build
```

Services:
| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Django API | http://localhost:8000 |
| Django Admin | http://localhost:8000/admin/ |

The backend container automatically runs `migrate` and `seed.py` on startup.

---

## Quickstart — Local Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start Postgres and Redis (or use Docker for just those)
docker run -d -p 5432:5432 -e POSTGRES_DB=playto -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres postgres:15-alpine
docker run -d -p 6379:6379 redis:7-alpine

# Create .env from example
cp ../.env.example ../.env

export DJANGO_SETTINGS_MODULE=config.settings
python manage.py migrate
python seed.py                   # Seed 3 merchants with credit history

# Three terminals:
python manage.py runserver       # Terminal 1 — Django dev server
celery -A config worker --loglevel=info   # Terminal 2 — Celery worker
celery -A config beat --loglevel=info     # Terminal 3 — Celery beat
```

### Frontend

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev
# Open http://localhost:5173
```

---

## Running Tests

```bash
cd backend
python manage.py test payouts.tests --verbosity=2
```

Tests included:
- `test_concurrency.py` — Two simultaneous 60% overdraw requests; exactly one wins
- `test_idempotency.py` — Same key replays; locked key → 409; expired key reusable; scoped per merchant
- `test_state_machine.py` — All legal/illegal transitions; `failed→completed` blocked
- Ledger integrity invariant: `SUM(credits) - SUM(debits) = balance`

The concurrency tests use `TransactionTestCase` (not `TestCase`) because `SELECT FOR UPDATE` requires real commits visible across threads — the standard test wrapper transaction would hide rows from other threads.

---

## API Reference

### Authentication
Pass `X-Merchant-Id: <merchant-uuid>` in every request header.  
(Production would use signed JWTs; this is simplified for the challenge.)

### Endpoints

#### `GET /api/v1/merchants/`
List all merchants with their balances.

#### `GET /api/v1/merchants/<merchant_id>/`
Full dashboard: balance, held balance, recent ledger entries, payout history.

#### `POST /api/v1/payouts/`
Create a payout request.

**Required headers:**
```
Idempotency-Key: <uuid>      # Merchant-supplied; same key → same response
X-Merchant-Id: <uuid>
Content-Type: application/json
```

**Body:**
```json
{
  "amount_paise": 50000,
  "bank_account_id": "uuid"
}
```

**Responses:**
| Status | Meaning |
|---|---|
| 201 | Payout created (or idempotent replay) |
| 400 | Missing header / validation error |
| 409 | Same key still in-flight |
| 422 | Insufficient balance |
| 503 | Merchant lock busy — retry |

#### `GET /api/v1/payouts/<payout_id>/`
Payout detail and current status.

---

## Payout Lifecycle

```
PENDING ──► PROCESSING ──► COMPLETED
                  │
                  └────────► FAILED ──► (funds returned to ledger)
```

Bank simulation (background worker):
- **70%** → COMPLETED (funds settled)
- **20%** → FAILED (funds returned atomically)
- **10%** → Hangs in PROCESSING → retry after 30s → up to 3 attempts → then FAILED

---

## Seed Data

Three merchants are seeded automatically:

| Merchant | Balance |
|---|---|
| Anika Design Studio | ₹5,150.00 |
| Rajan Freelance Dev | ₹12,450.00 |
| Priya SaaS Exports | ₹22,300.00 |

Run `python seed.py` to see the exact merchant UUIDs and bank account IDs printed to stdout.

---

## Key Design Decisions

### Money as `BigIntegerField` in paise
All amounts are stored as integers in paise. No `FloatField`, no `DecimalField`. Floating-point arithmetic on monetary values is a correctness bug waiting to happen (`0.1 + 0.2 ≠ 0.3`). Integer arithmetic is exact.

### Balance from DB aggregation, never Python arithmetic
`get_balance_paise()` runs a single `SUM(CASE WHEN credit THEN +amount ELSE -amount)` query. We never fetch ledger rows into Python and sum them — that creates a window where the balance is stale.

### `SELECT FOR UPDATE NOWAIT` for concurrency
When a payout is created, we lock the merchant row for the duration of the transaction. `NOWAIT` means concurrent requests fail-fast (503) instead of queuing — the client retries, which is the correct behavior for payment APIs.

### Idempotency via `unique_together` + locked sentinel
`get_or_create(key, merchant)` relies on a DB-level unique constraint. The `locked=True` sentinel means: "first request is in-flight." Second callers get 409. Once settled, the response is replayed verbatim forever (until the key expires after 24h).

### Atomic failure + fund return
When a payout fails, `transition_to(FAILED)` and the compensating credit ledger entry happen in the same `transaction.atomic()` block. Either both commit or neither does.

---

## Project Structure

```
playto-payout/
├── backend/
│   ├── config/               # Django settings, URLs, Celery
│   ├── payouts/
│   │   ├── models.py         # Merchant, LedgerEntry, Payout, IdempotencyKey
│   │   ├── views.py          # API views (concurrency + idempotency logic)
│   │   ├── serializers.py    # DRF serializers
│   │   ├── tasks.py          # Celery tasks (payout processor, retry sweep)
│   │   ├── urls.py
│   │   └── tests/
│   │       ├── test_concurrency.py
│   │       ├── test_idempotency.py
│   │       └── test_state_machine.py
│   ├── seed.py
│   ├── manage.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.jsx           # Main app + merchant selector + polling
│   │   ├── api.js            # API client
│   │   └── components/
│   │       ├── BalanceCards.jsx
│   │       ├── PayoutForm.jsx
│   │       ├── PayoutHistory.jsx
│   │       └── LedgerTable.jsx
│   ├── Dockerfile
│   └── nginx.conf
├── docker-compose.yml
├── .env.example
├── README.md
└── EXPLAINER.md
```
