#!/usr/bin/env bash
# End-to-end demo against a running server: ./scripts_demo.sh [base_url]
set -euo pipefail
B=${1:-http://localhost:8000}; WS=${WEBHOOK_SECRET:-dev-webhook-secret}
j() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
curl -s -X POST $B/auth/signup -H 'content-type: application/json' -d '{"email":"admin@example.com","full_name":"Admin","password":"password123"}' >/dev/null || true
AT=$(curl -s -X POST $B/auth/login -H 'content-type: application/json' -d '{"email":"admin@example.com","password":"password123"}' | j "['access_token']")
H="authorization: Bearer $AT"
T=$(curl -s -X POST $B/tests -H "$H" -H 'content-type: application/json' -d '{"name":"Lipid Profile '$RANDOM'"}' | j "['id']")
C=$(curl -s -X POST $B/centres -H "$H" -H 'content-type: application/json' -d '{"name":"EVE Central '$RANDOM'","location":"Delhi"}' | j "['id']")
curl -s -X PUT $B/centres/$C/tests -H "$H" -H 'content-type: application/json' -d "{\"test_id\":$T,\"price\":\"750.00\"}" >/dev/null
WHEN=$(date -u -v+3d +%Y-%m-%dT%H:%M:%SZ)
BK=$(curl -s -X POST $B/bookings -H "$H" -H 'content-type: application/json' -d "{\"centre_id\":$C,\"test_id\":$T,\"appointment_at\":\"$WHEN\"}" | j "['id']")
REF=$(curl -s -X POST $B/payments/ -H "$H" -H 'content-type: application/json' -d "{\"booking_id\":$BK,\"async_mode\":true}" | j "['provider_reference']")
BODY="{\"event_id\":\"evt_$RANDOM\",\"provider_reference\":\"$REF\",\"status\":\"SUCCESS\"}"
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$WS" | awk '{print $2}')
echo "webhook #1:"; curl -s -X POST $B/payments/webhook/ -H "X-Webhook-Signature: $SIG" -H 'content-type: application/json' -d "$BODY"; echo
echo "webhook #2 (same event):"; curl -s -X POST $B/payments/webhook/ -H "X-Webhook-Signature: $SIG" -H 'content-type: application/json' -d "$BODY"; echo
