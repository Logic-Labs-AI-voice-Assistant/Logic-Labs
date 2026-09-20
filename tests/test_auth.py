import os
import tempfile

fd, db_path = tempfile.mkstemp(prefix="voiceit_auth_", suffix=".db")
os.close(fd)
os.environ["DATABASE_PATH"] = db_path

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_customer_register_and_login_flow():
    payload = {
        "full_name": "Aisha Khan",
        "email": "aisha@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!",
    }

    register_response = client.post("/api/auth/register", json=payload)
    assert register_response.status_code == 201, register_response.text
    data = register_response.json()
    assert data["email"] == payload["email"]
    assert "password" not in data

    login_response = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login_response.status_code == 200, login_response.text
    user = login_response.json()
    assert user["email"] == payload["email"]
    assert "password" not in user

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200, me_response.text
    assert me_response.json()["email"] == payload["email"]

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200, logout_response.text

    me_after_logout = client.get("/api/auth/me")
    assert me_after_logout.status_code == 401, me_after_logout.text


def test_login_invalid_password_and_duplicate_registration():
    payload = {
        "full_name": "Sam Green",
        "email": "sam@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!",
    }

    register_response = client.post("/api/auth/register", json=payload)
    assert register_response.status_code == 201, register_response.text

    invalid_login = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": "WrongPassword1!"},
    )
    assert invalid_login.status_code == 401, invalid_login.text

    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.status_code == 409, duplicate.text
