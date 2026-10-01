import os

# Must be set before the app is imported.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ADMIN_EMAILS"] = "admin@example.com"
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-at-least-32-bytes-long"

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.security import sign_webhook


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:  # lifespan creates tables
        yield c


def _signup_login(client, email, password="password123"):
    client.post("/auth/signup", json={"email": email, "full_name": "Test User", "password": password})
    r = client.post("/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def user_h(client):
    return _signup_login(client, "user@example.com")


@pytest.fixture()
def other_h(client):
    return _signup_login(client, "other@example.com")


@pytest.fixture()
def admin_h(client):
    return _signup_login(client, "admin@example.com")


@pytest.fixture()
def catalogue(client, admin_h):
    """One centre offering one test at 499.50."""
    t = client.post("/tests", json={"name": "CBC"}, headers=admin_h).json()
    c = client.post("/centres", json={"name": "EVE Central", "location": "Delhi"}, headers=admin_h).json()
    client.put(f"/centres/{c['id']}/tests", json={"test_id": t["id"], "price": "499.50"}, headers=admin_h)
    return {"centre_id": c["id"], "test_id": t["id"]}


def future(days=3):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture()
def booking(client, user_h, catalogue):
    r = client.post("/bookings", json={**catalogue, "appointment_at": future()}, headers=user_h)
    assert r.status_code == 201, r.text
    return r.json()


def post_webhook(client, payload: dict, signature: str | None = "auto"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Webhook-Signature"] = sign_webhook(body)
    elif signature:
        headers["X-Webhook-Signature"] = signature
    return client.post("/payments/webhook/", content=body, headers=headers)
