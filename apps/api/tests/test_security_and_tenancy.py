import uuid

from fastapi.testclient import TestClient

from app.main import app


def register(client: TestClient, suffix: str):
    payload = {
        "name": f"Facility {suffix}", "commercial_reg": f"CR-{suffix}",
        "slug": f"facility-{suffix}", "admin_name": "Security Admin",
        "username": "admin", "password": "StrongPassword!2026", "seats": 3,
    }
    response = client.post("/api/v1/facilities/register", json=payload)
    assert response.status_code == 200
    return payload


def login(client: TestClient, facility: str):
    response = client.post("/api/v1/auth/login", json={"facility": facility, "username": "admin", "password": "StrongPassword!2026"})
    assert response.status_code == 200
    assert response.cookies.get("medify_access")


def test_secure_headers_and_cookie_session():
    with TestClient(app) as client:
        suffix = uuid.uuid4().hex[:10]
        account = register(client, suffix)
        login(client, account["slug"])
        response = client.get("/api/v1/me")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_facility_data_isolation():
    with TestClient(app) as first, TestClient(app) as second:
        a = register(first, uuid.uuid4().hex[:10])
        b = register(second, uuid.uuid4().hex[:10])
        login(first, a["slug"]); login(second, b["slug"])
        assert first.post("/api/v1/clinics", json={"name": "Tenant A Clinic"}).status_code == 200
        assert second.post("/api/v1/clinics", json={"name": "Tenant B Clinic"}).status_code == 200
        names_a = {row["name"] for row in first.get("/api/v1/clinics").json()["data"]}
        names_b = {row["name"] for row in second.get("/api/v1/clinics").json()["data"]}
        assert "Tenant A Clinic" in names_a and "Tenant B Clinic" not in names_a
        assert "Tenant B Clinic" in names_b and "Tenant A Clinic" not in names_b


def test_invalid_token_is_rejected():
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401
