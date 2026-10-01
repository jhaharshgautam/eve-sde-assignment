def test_signup_and_login_flow(client):
    r = client.post("/auth/signup", json={"email": "A@Example.com", "full_name": "A", "password": "password123"})
    assert r.status_code == 201
    assert r.json()["email"] == "a@example.com"
    assert "password" not in r.text
    login = client.post("/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "a@example.com"


def test_duplicate_signup_conflict(client):
    body = {"email": "a@example.com", "full_name": "A", "password": "password123"}
    assert client.post("/auth/signup", json=body).status_code == 201
    assert client.post("/auth/signup", json=body).status_code == 409


def test_signup_validation(client):
    assert client.post("/auth/signup", json={"email": "nope", "full_name": "A", "password": "password123"}).status_code == 422
    assert client.post("/auth/signup", json={"email": "a@example.com", "full_name": "A", "password": "short"}).status_code == 422
    assert client.post("/auth/signup", json={"email": "a@example.com", "full_name": " ", "password": "password123"}).status_code == 422


def test_login_wrong_password_and_unknown_user(client, user_h):
    assert client.post("/auth/login", json={"email": "user@example.com", "password": "wrongpass1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "ghost@example.com", "password": "password123"}).status_code == 401


def test_invalid_token_rejected(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_catalog_writes_require_admin(client, user_h, admin_h):
    assert client.post("/centres", json={"name": "X", "location": "Y"}).status_code == 401
    assert client.post("/centres", json={"name": "X", "location": "Y"}, headers=user_h).status_code == 403
    assert client.post("/centres", json={"name": "X", "location": "Y"}, headers=admin_h).status_code == 201


def test_centres_list_filter_and_pagination(client, admin_h, catalogue):
    client.post("/centres", json={"name": "EVE North", "location": "Noida"}, headers=admin_h)
    r = client.get("/centres")
    assert r.json()["total"] == 2
    assert client.get("/centres", params={"location": "noi"}).json()["total"] == 1
    assert client.get("/centres", params={"test_id": catalogue["test_id"]}).json()["total"] == 1
    page = client.get("/centres", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["total"] == 2
    assert client.get("/centres", params={"limit": 0}).status_code == 422


def test_centre_shows_tests_and_price_update(client, admin_h, catalogue):
    c = client.get(f"/centres/{catalogue['centre_id']}").json()
    assert c["tests"][0]["test_name"] == "CBC" and c["tests"][0]["price"] in ("499.50", 499.5)
    r = client.put(
        f"/centres/{catalogue['centre_id']}/tests",
        json={"test_id": catalogue["test_id"], "price": "600"},
        headers=admin_h,
    )
    assert len(r.json()["tests"]) == 1  # updated, not duplicated


def test_catalog_not_found_and_conflicts(client, admin_h, catalogue):
    assert client.get("/centres/9999").status_code == 404
    assert client.put("/centres/9999/tests", json={"test_id": 1, "price": "1"}, headers=admin_h).status_code == 404
    assert client.put(f"/centres/{catalogue['centre_id']}/tests", json={"test_id": 9999, "price": "1"}, headers=admin_h).status_code == 404
    assert client.put(f"/centres/{catalogue['centre_id']}/tests", json={"test_id": catalogue["test_id"], "price": "-5"}, headers=admin_h).status_code == 422
    assert client.post("/tests", json={"name": "CBC"}, headers=admin_h).status_code == 409
