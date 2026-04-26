# EXPLAINER.md — Playto Payout Engine

---

## 1. The Ledger — Balance Calculation Query

### The query

```python
# payouts/models.py — Merchant.get_balance_paise()

result = LedgerEntry.objects.filter(merchant=self).aggregate(
    balance=Coalesce(
        Sum(
            Case(
                When(entry_type=LedgerEntry.CREDIT, then=F("amount_paise")),
                When(entry_type=LedgerEntry.DEBIT,  then=-F("amount_paise")),
                output_field=BigIntegerField(),
            )
        ),
        Value(0),
        output_field=BigIntegerField(),
    )
)
return result["balance"]
```

This emits a single SQL statement:

```sql
SELECT COALESCE(
  SUM(CASE
    WHEN entry_type = 'credit' THEN  amount_paise
    WHEN entry_type = 'debit'  THEN -amount_paise
  END),
  0
) AS balance
FROM ledger_entries
WHERE merchant_id = %s;
```

### Why this model

**Immutable append-only ledger, not a mutable balance column.**  
A mutable `balance` column on `Merchant` is a concurrency footgun: two concurrent updates can race and produce the wrong final value even with transactions, unless every update uses `F()` expressions. And even then, you lose history.

An append-only ledger solves both problems:
1. **Auditability** — every credit and debit is a permanent row. You can reconstruct the full history, replay it for audits, or recompute the balance from scratch to verify integrity.
2. **Concurrency** — `INSERT` doesn't conflict with other `INSERT`s the way `UPDATE balance = balance + X` can under high contention. The aggregate query always reflects committed state.

**`BigIntegerField` in paise, never `FloatField`.**  
`0.1 + 0.2 = 0.30000000000000004` in IEEE 754. For monetary values that's a correctness bug. Paise are integers — ₹1.50 = 150 paise. Integer arithmetic is exact. `DecimalField` would also work but adds serialization overhead and the ORM tends to produce `NUMERIC` columns that interact awkwardly with `SUM`.

**Balance computed at the database, not in Python.**  
The alternative — fetching all ledger rows and summing in Python — creates a window between the SELECT and the sum where new rows can be inserted by concurrent transactions. The DB-level `SUM` sees the exact committed state at the moment the transaction runs.

---

## 2. The Lock — Preventing Concurrent Overdraw

### The exact code

```python
# payouts/views.py — CreatePayoutView.post()

with transaction.atomic():
    # Acquire a row-level lock on the Merchant record.
    # NOWAIT = fail immediately if another transaction holds it.
    try:
        locked_merchant = Merchant.objects.select_for_update(
            nowait=True
        ).get(id=merchant.id)
    except OperationalError:
        return Response(
            {"error": "Another payout is being processed. Retry in a moment."},
            status=503
        )

    # Balance aggregation runs inside the same serialized transaction.
    # No other transaction can modify ledger_entries for this merchant
    # while we hold the merchant row lock.
    balance = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
        balance=Coalesce(
            Sum(Case(
                When(entry_type=LedgerEntry.CREDIT, then=F("amount_paise")),
                When(entry_type=LedgerEntry.DEBIT,  then=-F("amount_paise")),
                output_field=BigIntegerField(),
            )),
            Value(0),
            output_field=BigIntegerField(),
        )
    )["balance"]

    if balance < amount_paise:
        return Response({"error": "Insufficient balance", ...}, status=422)

    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type=LedgerEntry.DEBIT, ...)
    # Lock released when transaction.atomic() block exits
```

### The database primitive

**`SELECT FOR UPDATE NOWAIT`** — a PostgreSQL row-level exclusive lock.

When thread A executes `SELECT ... FOR UPDATE NOWAIT` on merchant row M, PostgreSQL grants it an exclusive lock on that row. Any concurrent `SELECT ... FOR UPDATE` by thread B on the same row immediately raises an error (instead of blocking). Thread B's request returns 503; the client retries.

**Why lock the Merchant row, not the LedgerEntry rows?**

Locking existing `LedgerEntry` rows doesn't help: the new rows that thread B would insert don't exist yet when thread A checks the balance, so there's no row to lock. This is the *phantom read* problem. By locking the Merchant row instead, we serialize all payout requests for a given merchant at the merchant level — thread B can't even check the balance until thread A's transaction commits.

**Why `NOWAIT`?**

Without `NOWAIT`, thread B would block until thread A finishes (potentially several seconds for a bank API call). `NOWAIT` makes thread B fail fast with a 503, which is the correct behavior for a payment API: the client gets an immediate signal to retry rather than hanging.

**Why not Python-level locking (threading.Lock, Redis SETNX)?**

Python-level locks don't survive process restarts and don't work across multiple gunicorn workers. Redis SETNX is reasonable but adds a dependency and introduces distributed lock expiry edge cases. The PostgreSQL row lock is transactional by definition — it's automatically released when the transaction commits or rolls back, even on crash.

---

## 3. The Idempotency — How It Works

### How the system recognizes a seen key

```python
# payouts/views.py

idem_key, created = IdempotencyKey.objects.get_or_create(
    key=idempotency_key_val,
    merchant=merchant,
    defaults={
        "locked": True,
        "response_body": {},
        "response_status": 0,
        "expires_at": expires_at,
    },
)
```

`get_or_create` relies on a **database-level `UNIQUE` constraint** on `(key, merchant_id)` in the `idempotency_keys` table. If two requests race, only one `INSERT` succeeds — PostgreSQL enforces uniqueness atomically. The loser falls back to a `SELECT`, gets `created=False`, and knows the key already exists.

If `created=False`:
- **`locked=True`** → first request is still in-flight → return 409 Conflict
- **`locked=False`** → first request completed → replay `response_body` with `response_status` verbatim
- **`is_expired()`** → key is past 24h TTL → delete it, treat as new

### What happens if the first request is still in-flight when the second arrives

The first request sets `locked=True` when it creates the key. It only sets `locked=False` in the same `transaction.atomic()` block that creates the payout and ledger debit — so `locked` is a reliable sentinel for "in-progress."

If request B arrives while request A is inside `transaction.atomic()`:
1. `get_or_create` returns `created=False` and the existing row with `locked=True`
2. Request B returns **409 Conflict** immediately: `"A request with this idempotency key is already in progress. Retry in a moment."`
3. Request A completes, sets `locked=False`, stores the response
4. Any subsequent call with the same key gets the stored response (201 or 422 etc.) replayed exactly

If request A crashes mid-flight, the key stays `locked=True` indefinitely. The cleanup task `cleanup_expired_idempotency_keys` runs hourly and removes keys past `expires_at` (24h). In production you'd want a shorter stuck-lock timeout (e.g., 5 minutes) based on your P99 request latency.

**Key scoping:** The `unique_together` constraint is on `(key, merchant)`, not just `key`. A key `"abc-123"` from merchant A and the same string from merchant B are independent rows — they don't interfere.

---

## 4. The State Machine — Where Illegal Transitions Are Blocked

### The check

```python
# payouts/models.py — Payout.transition_to()

VALID_TRANSITIONS: dict[str, list[str]] = {
    PENDING:    [PROCESSING],
    PROCESSING: [COMPLETED, FAILED],
    COMPLETED:  [],   # Terminal — no exits
    FAILED:     [],   # Terminal — no exits
}

def transition_to(self, new_status: str) -> None:
    valid = self.VALID_TRANSITIONS.get(self.status, [])
    if new_status not in valid:
        raise InvalidTransitionError(
            f"Illegal transition: '{self.status}' → '{new_status}'. "
            f"Valid targets from '{self.status}': {valid}"
        )
    old_status = self.status
    self.status = new_status
    self.save(update_fields=["status", "updated_at"])
```

`transition_to` is the **single choke point** for all status changes. No code in the codebase sets `payout.status` directly and calls `save()` — every status change goes through this method. That means the state machine is enforced unconditionally.

**`failed → completed` specifically:**  
`VALID_TRANSITIONS[FAILED] = []` — the empty list means no transition out of `FAILED` is legal. `transition_to(COMPLETED)` from `FAILED` raises `InvalidTransitionError`. This is the critical one: a failed payout must never become completed, which would imply the funds both returned (via the compensating credit) *and* settled, creating phantom money.

**Why a dict of valid targets, not a set of forbidden pairs?**  
Allowlist > denylist. A denylist approach (block specific bad transitions) requires you to enumerate all the bad ones. It's easy to miss one. An allowlist approach means: if it's not in this list, it doesn't happen. New statuses added to the model are automatically blocked from all transitions until explicitly added.

---

## 5. The AI Audit — Where AI Gave Wrong Code

### What AI generated

When writing the idempotency layer, the AI initially produced this pattern:

```python
# WRONG — what AI wrote
try:
    existing = IdempotencyKey.objects.get(key=idempotency_key_val, merchant=merchant)
    # Key exists — return the stored response
    return Response(existing.response_body, status=existing.response_status)
except IdempotencyKey.DoesNotExist:
    # New key — create it and proceed
    idem_key = IdempotencyKey.objects.create(
        key=idempotency_key_val,
        merchant=merchant,
        locked=True,
        expires_at=expires_at,
    )
```

### The bug

This is a classic **check-then-act race condition (TOCTOU)**:

1. Request A: `objects.get(...)` → `DoesNotExist` → falls through to `create()`
2. Request B: `objects.get(...)` → `DoesNotExist` (same microsecond, A hasn't committed yet) → falls through to `create()`
3. Request A: `objects.create(...)` → succeeds, inserts row
4. Request B: `objects.create(...)` → **IntegrityError** (unique constraint violation) — unhandled crash

Under high concurrency (or just a slow DB), both requests see the key as absent and both try to insert. One crashes with an unhandled `IntegrityError`. The merchant gets a 500. Even worse: if the `create` was wrapped in a try/except that silently ignored `IntegrityError`, both requests could proceed and both would create a payout — the idempotency guarantee is broken entirely.

### What I replaced it with

```python
# CORRECT — what I wrote
idem_key, created = IdempotencyKey.objects.get_or_create(
    key=idempotency_key_val,
    merchant=merchant,
    defaults={
        "locked": True,
        "response_body": {},
        "response_status": 0,
        "expires_at": expires_at,
    },
)
```

`get_or_create` is **atomic at the database level**. Django's implementation uses `INSERT ... ON CONFLICT DO NOTHING` (on PostgreSQL) or equivalent, backed by the `UNIQUE` constraint on `(key, merchant_id)`. If two requests race:
- Exactly one `INSERT` succeeds → `created=True`
- The other falls back to `SELECT` → `created=False`, reads the existing row

No crash, no duplicate. The uniqueness contract is enforced by PostgreSQL, not by application-level ordering.

**The second subtle bug AI introduced** was in the balance check inside the payout creation path. The original generated code was:

```python
# WRONG — Python arithmetic on a fetched value
merchant_obj = Merchant.objects.get(id=merchant_id)
balance = merchant_obj.get_balance_paise()  # fetches and computes in Python
# ... 50ms later, after validation ...
if balance < amount_paise:
    return insufficient_balance_response

payout = Payout.objects.create(...)  # TOCTOU window: balance may have changed
```

Between the `get_balance_paise()` call and the `Payout.objects.create()`, another concurrent transaction could have debited the same merchant's balance. The check is stale.

**What I replaced it with:** Move the entire balance check and payout creation inside `transaction.atomic()` with `SELECT FOR UPDATE NOWAIT` on the merchant row. The balance aggregation runs on the locked, consistent snapshot. There is no window.

---

## Summary

The hardest part of this engine is not the happy path — it's the gap between "it works in testing" and "it's correct under production concurrency." The three places this most often fails in real systems:

1. **Ledger balance from stale Python state** → fixed by DB-level aggregation inside the locked transaction
2. **Check-then-act on idempotency keys** → fixed by `get_or_create` backed by a UNIQUE constraint
3. **Partial failure on fund return** → fixed by atomic `transition_to(FAILED)` + compensating credit in one `transaction.atomic()` block

Every one of these was either generated incorrectly by AI or required deliberate adjustment from the first draft. The EXPLAINER exists because understanding *why* the correct version is correct matters more than having correct code you can't explain.
