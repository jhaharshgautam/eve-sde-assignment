from tests.conftest import post_webhook


def pay(client, headers, booking_id, **kw):
    return client.post("/payments/", json={"booking_id": booking_id, **kw}, headers=headers)


def test_successful_payment_confirms_booking(client, booking, user_h):
    r = pay(client, user_h, booking["id"], simulate_outcome="SUCCESS")
    assert r.status_code == 201 and r.json()["status"] == "SUCCESS"
    detail = client.get(f"/bookings/{booking['id']}", headers=user_h).json()
    assert detail["status"] == "CONFIRMED" and len(detail["payments"]) == 1


def test_failed_payment_marks_booking_failed_and_allows_retry(client, booking, user_h):
    r = pay(client, user_h, booking["id"], simulate_outcome="FAILED")
    assert r.json()["status"] == "FAILED" and r.json()["failure_reason"]
    assert client.get(f"/bookings/{booking['id']}", headers=user_h).json()["status"] == "FAILED"
    retry = pay(client, user_h, booking["id"], simulate_outcome="SUCCESS")
    assert retry.status_code == 201
    detail = client.get(f"/bookings/{booking['id']}", headers=user_h).json()
    assert detail["status"] == "CONFIRMED" and len(detail["payments"]) == 2


def test_cannot_pay_twice_or_for_cancelled(client, booking, user_h):
    assert pay(client, user_h, booking["id"], simulate_outcome="SUCCESS").status_code == 201
    assert pay(client, user_h, booking["id"], simulate_outcome="SUCCESS").status_code == 409
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_h)
    assert pay(client, user_h, booking["id"]).status_code == 409


def test_payment_auth_and_ownership(client, booking, other_h):
    assert client.post("/payments/", json={"booking_id": booking["id"]}).status_code == 401
    assert pay(client, other_h, booking["id"]).status_code == 404


def test_payment_invalid_input(client, user_h, booking):
    assert pay(client, user_h, 99999).status_code == 404
    assert client.post("/payments/", json={}, headers=user_h).status_code == 422
    assert pay(client, user_h, booking["id"], simulate_outcome="MAYBE").status_code == 422


def test_random_outcome_is_valid(client, booking, user_h):
    assert pay(client, user_h, booking["id"]).json()["status"] in {"SUCCESS", "FAILED"}


# ---------------- webhook ----------------
def _async_payment(client, booking, user_h):
    r = pay(client, user_h, booking["id"], async_mode=True)
    assert r.json()["status"] == "PENDING"
    return r.json()["provider_reference"]


def test_webhook_success_confirms_booking(client, booking, user_h):
    ref = _async_payment(client, booking, user_h)
    r = post_webhook(client, {"event_id": "evt_1", "provider_reference": ref, "status": "SUCCESS"})
    assert r.status_code == 200
    assert r.json() == {"result": "applied", "payment_status": "SUCCESS", "booking_status": "CONFIRMED"}


def test_webhook_failed_fails_booking(client, booking, user_h):
    ref = _async_payment(client, booking, user_h)
    r = post_webhook(client, {"event_id": "evt_1", "provider_reference": ref, "status": "FAILED", "failure_reason": "insufficient funds"})
    assert r.json()["booking_status"] == "FAILED"


def test_webhook_is_idempotent(client, booking, user_h):
    ref = _async_payment(client, booking, user_h)
    payload = {"event_id": "evt_dup", "provider_reference": ref, "status": "SUCCESS"}
    results = [post_webhook(client, payload) for _ in range(3)]
    assert [r.status_code for r in results] == [200, 200, 200]
    assert [r.json()["result"] for r in results] == ["applied", "duplicate", "duplicate"]
    detail = client.get(f"/bookings/{booking['id']}", headers=user_h).json()
    assert detail["status"] == "CONFIRMED"
    assert len(detail["payments"]) == 1  # no duplicate payments
    assert client.get("/bookings", headers=user_h).json()["total"] == 1  # no duplicate bookings


def test_webhook_for_already_final_payment_does_not_flip_state(client, booking, user_h):
    r = pay(client, user_h, booking["id"], simulate_outcome="SUCCESS").json()
    # A *different* event claims failure after success: must be ignored, booking stays CONFIRMED.
    res = post_webhook(client, {"event_id": "evt_late", "provider_reference": r["provider_reference"], "status": "FAILED"})
    assert res.json()["result"] == "ignored"
    assert res.json()["booking_status"] == "CONFIRMED" and res.json()["payment_status"] == "SUCCESS"


def test_webhook_does_not_resurrect_cancelled_booking(client, booking, user_h):
    ref = _async_payment(client, booking, user_h)
    client.post(f"/bookings/{booking['id']}/cancel", headers=user_h)
    res = post_webhook(client, {"event_id": "evt_c", "provider_reference": ref, "status": "SUCCESS"})
    assert res.json()["booking_status"] == "CANCELLED"


def test_webhook_security_and_validation(client, booking, user_h):
    ref = _async_payment(client, booking, user_h)
    good = {"event_id": "e", "provider_reference": ref, "status": "SUCCESS"}
    assert post_webhook(client, good, signature=None).status_code == 401
    assert post_webhook(client, good, signature="deadbeef").status_code == 401
    assert post_webhook(client, {"event_id": "e", "provider_reference": ref, "status": "REFUNDED"}).status_code == 422
    assert post_webhook(client, {"provider_reference": ref, "status": "SUCCESS"}).status_code == 422
    unknown = post_webhook(client, {"event_id": "e2", "provider_reference": "sim_nope", "status": "SUCCESS"})
    assert unknown.status_code == 404
    # Rejected requests must not have touched state or burned the event id.
    assert post_webhook(client, good).json()["result"] == "applied"


def test_webhook_malformed_json(client):
    from app.security import sign_webhook

    body = b"{not json"
    r = client.post("/payments/webhook/", content=body, headers={"X-Webhook-Signature": sign_webhook(body)})
    assert r.status_code == 422
