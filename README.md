# EVE Diagnostics Booking API

Backend service for booking diagnostic tests with a simulated payment provider and an **idempotent, signed payment webhook**.

**Stack:** Python 3.12+, FastAPI, SQLAlchemy 2, PostgreSQL (SQLite for tests), JWT (PyJWT), Argon2 password hashing, pytest, Docker.

## Run it

### Docker (recommended)
```bash
docker compose up --build        # API on http://localhost:8000, Postgres 16 included
```
Swagger UI: http://localhost:8000/docs · OpenAPI: `/openapi.json`

Users whose email is in `ADMIN_EMAILS` (default `admin@example.com`) become admins on signup and can manage the catalogue. Run `./demo.sh` for an end-to-end walkthrough (signup → catalogue → booking → async payment → webhook twice).

### Locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # point DATABASE_URL at your Postgres (or sqlite+pysqlite:///./dev.db)
uvicorn app.main:app --reload
```

### Tests
```bash
pytest -q        # 32 tests, in-memory SQLite, no external services
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/signup` | – | Create account |
| POST | `/auth/login` | – | Returns JWT |
| GET | `/auth/me` | user | Current user |
| POST | `/tests` | admin | Add a test to the catalogue |
| GET | `/tests` | – | List tests (paginated) |
| POST | `/centres` | admin | Create centre |
| GET | `/centres` | – | List; filters `location`, `test_id`; `limit`/`offset` |
| GET | `/centres/{id}` | – | Centre with its tests and prices |
| PUT | `/centres/{id}/tests` | admin | Offer a test at a price (upsert) |
| POST | `/bookings` | user | Create booking (`PENDING`) |
| GET | `/bookings` | user | My bookings; filter `status`; paginated |
| GET | `/bookings/{id}` | user | My booking + its payments |
| POST | `/bookings/{id}/cancel` | user | Cancel (idempotent) |
| POST | `/payments/` | user | Mock payment |
| POST | `/payments/webhook/` | HMAC signature | Provider status callback |

### Example requests
```bash
# signup + login
curl -X POST localhost:8000/auth/signup -H 'content-type: application/json' \
  -d '{"email":"jane@example.com","full_name":"Jane","password":"password123"}'
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"jane@example.com","password":"password123"}' | jq -r .access_token)

# book (appointment_at must be timezone-aware and in the future)
curl -X POST localhost:8000/bookings -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"centre_id":1,"test_id":1,"appointment_at":"2026-11-01T10:00:00+05:30"}'

# pay (random outcome; force one with "simulate_outcome": "SUCCESS" | "FAILED";
#      "async_mode": true leaves it PENDING until the webhook arrives)
curl -X POST localhost:8000/payments/ -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"booking_id":1,"simulate_outcome":"SUCCESS"}'

# webhook: signature = hex HMAC-SHA256(WEBHOOK_SECRET, raw_body) in X-Webhook-Signature
BODY='{"event_id":"evt_123","provider_reference":"sim_abc...","status":"SUCCESS"}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')
curl -X POST localhost:8000/payments/webhook/ -H "X-Webhook-Signature: $SIG" -H 'content-type: application/json' -d "$BODY"
```

## Design

```
app/
  main.py            app + lifespan (creates tables)
  config.py          env-driven settings
  models.py          SQLAlchemy models
  schemas.py         Pydantic request/response models
  security.py        password hashing, JWT, webhook HMAC
  deps.py            auth dependencies (current user, admin)
  services/payments.py   ALL payment/booking state transitions (single place, easy to test)
  routers/           thin HTTP layer: auth, catalog, bookings, payments
```

### Schema
- **users** `(id, email UNIQUE, full_name, password_hash, is_admin)`
- **diagnostic_centres** `(id, name, location)` · UNIQUE(name, location)
- **diagnostic_tests** `(id, name UNIQUE, description)` – catalogue
- **centre_tests** `(id, centre_id, test_id, price)` · UNIQUE(centre_id, test_id), CHECK price ≥ 0 – a centre offers a test at its own price (M:N with an attribute)
- **bookings** `(id, user_id, centre_test_id, centre_id, test_id, appointment_at, amount, status)` – `amount` is a **price snapshot**, so later price changes don't alter existing bookings. A partial unique index on `(user_id, centre_test_id, appointment_at)` for non-cancelled/non-failed bookings stops duplicate live bookings (also under races).
- **payments** `(id, booking_id, provider_reference UNIQUE, amount, status, failure_reason)` – one row per attempt, so retry history is kept.
- **webhook_events** `(id, event_id UNIQUE, payment_id, reported_status, outcome)` – ledger of processed events; the unique `event_id` is the idempotency guard.

Money uses `NUMERIC(10,2)` / `Decimal`, never floats. Timestamps are stored as UTC and always returned timezone-aware.

### State machine
- Booking: `PENDING → CONFIRMED` (payment success) · `PENDING → FAILED` (payment failed) · `FAILED → CONFIRMED` (successful retry) · `PENDING|CONFIRMED → CANCELLED`.
- Payment: `PENDING → SUCCESS | FAILED`; final states are terminal.

### Webhook idempotency & safety
1. Signature (HMAC-SHA256 over the raw body, constant-time compare) is verified first → `401` otherwise.
2. The payment row is locked (`SELECT … FOR UPDATE`), then `event_id` is looked up in `webhook_events`; a repeat returns `200 {"result":"duplicate"}` and changes nothing (200 so the provider stops retrying).
3. If two identical deliveries race, the `UNIQUE(event_id)` constraint rejects the second insert; it's caught and returned as a duplicate.
4. A *different* event that contradicts a final payment state (e.g. late `FAILED` after `SUCCESS`) is recorded but **ignored** – it can't flip state. A success arriving for a cancelled booking marks the payment but leaves the booking `CANCELLED` (logged as needing a refund).
5. Unknown `provider_reference` → 404 without consuming the `event_id`, so a provider retry can still succeed later.

### Edge cases handled
Invalid/missing/expired JWT (401); non-admin writes (403); other users' bookings return 404 (no id probing); invalid/unknown ids (404/422); past or timezone-naive appointment (422); booking a test the centre doesn't offer (404); duplicate slot (409); paying a confirmed/cancelled booking or with a payment already in flight (409); failed payment then retry; bad webhook signature/payload (401/422); duplicate and out-of-order webhooks; duplicate email signup (409, also race-safe); login timing doesn't reveal whether an email exists.

## Assumptions
- Anyone can sign up; admins are bootstrapped via `ADMIN_EMAILS` and manage the catalogue. Centres/tests are publicly readable.
- A booking's amount is the centre's price at booking time. One test per booking.
- A `FAILED` booking may be paid again (new payment attempt); cancelled bookings cannot.
- The mock gateway approves 80% of payments randomly (`MOCK_PAYMENT_SUCCESS_RATE`); `simulate_outcome` makes it deterministic for tests/demos.
- Tables are created at startup with `create_all` for simplicity.
- Tests run on SQLite; `SELECT FOR UPDATE` is a no-op there, so the row-locking path is only exercised on PostgreSQL (the unique constraints still guard correctness on both).

## What I'd improve with more time
- Alembic migrations instead of `create_all`; run tests against real Postgres in CI (a concurrency test with parallel webhook deliveries).
- Refresh tokens / token revocation, email verification, login rate limiting (e.g. Redis + slowapi).
- Slot capacity per centre (prevent overbooking), opening hours, and expiring stale `PENDING` bookings via a Celery beat job.
- Real refund flow when a paid booking is cancelled or a late success hits a cancelled booking.
- Webhook processing via a queue with retries/dead-lettering; webhook secret rotation; replay window using a signed timestamp.
- Redis caching for centre listings; request-ID middleware; metrics.
