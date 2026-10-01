from tests.conftest import future


def test_create_booking_snapshots_price(client, user_h, catalogue):
    r = client.post("/bookings", json={**catalogue, "appointment_at": future()}, headers=user_h)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "PENDING" and float(body["amount"]) == 499.5


def test_booking_requires_auth(client, catalogue):
    assert client.post("/bookings", json={**catalogue, "appointment_at": future()}).status_code == 401


def test_booking_validation(client, user_h, catalogue):
    past = client.post("/bookings", json={**catalogue, "appointment_at": future(-1)}, headers=user_h)
    assert past.status_code == 422
    naive = client.post("/bookings", json={**catalogue, "appointment_at": "2099-01-01T10:00:00"}, headers=user_h)
    assert naive.status_code == 422
    bad = client.post("/bookings", json={**catalogue, "appointment_at": "tomorrow"}, headers=user_h)
    assert bad.status_code == 422
    missing = client.post("/bookings", json={"centre_id": 1}, headers=user_h)
    assert missing.status_code == 422


def test_booking_unknown_offering(client, user_h, catalogue):
    r = client.post("/bookings", json={"centre_id": catalogue["centre_id"], "test_id": 9999, "appointment_at": future()}, headers=user_h)
    assert r.status_code == 404


def test_duplicate_active_slot_rejected(client, user_h, catalogue):
    when = future()
    assert client.post("/bookings", json={**catalogue, "appointment_at": when}, headers=user_h).status_code == 201
    assert client.post("/bookings", json={**catalogue, "appointment_at": when}, headers=user_h).status_code == 409


def test_slot_reusable_after_cancel(client, user_h, catalogue):
    when = future()
    b = client.post("/bookings", json={**catalogue, "appointment_at": when}, headers=user_h).json()
    client.post(f"/bookings/{b['id']}/cancel", headers=user_h)
    assert client.post("/bookings", json={**catalogue, "appointment_at": when}, headers=user_h).status_code == 201


def test_cannot_see_or_modify_other_users_booking(client, booking, other_h):
    assert client.get(f"/bookings/{booking['id']}", headers=other_h).status_code == 404
    assert client.post(f"/bookings/{booking['id']}/cancel", headers=other_h).status_code == 404
    assert client.get("/bookings", headers=other_h).json()["total"] == 0


def test_invalid_booking_id(client, user_h):
    assert client.get("/bookings/99999", headers=user_h).status_code == 404
    assert client.get("/bookings/abc", headers=user_h).status_code == 422


def test_cancel_is_idempotent(client, booking, user_h):
    assert client.post(f"/bookings/{booking['id']}/cancel", headers=user_h).json()["status"] == "CANCELLED"
    assert client.post(f"/bookings/{booking['id']}/cancel", headers=user_h).json()["status"] == "CANCELLED"


def test_list_filter_by_status(client, booking, user_h):
    assert client.get("/bookings", params={"status": "PENDING"}, headers=user_h).json()["total"] == 1
    assert client.get("/bookings", params={"status": "CONFIRMED"}, headers=user_h).json()["total"] == 0
    assert client.get("/bookings", params={"status": "NOPE"}, headers=user_h).status_code == 422
