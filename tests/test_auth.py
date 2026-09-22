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


def test_tickets_flow():
    # Register and login a user
    payload = {
        "full_name": "Ticket User",
        "email": "ticketuser@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!",
    }
    client.post("/api/auth/register", json=payload)
    client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})

    # Create ticket
    create_res = client.post("/api/tickets", json={"issue": "VPN connection failing"})
    assert create_res.status_code == 200, create_res.text
    ticket_data = create_res.json()
    assert ticket_data["issue"] == "VPN connection failing"
    assert ticket_data["status"] == "open"
    ticket_id = ticket_data["id"]

    # Get tickets
    get_res = client.get("/api/tickets")
    assert get_res.status_code == 200, get_res.text
    tickets = get_res.json()
    assert len(tickets) >= 1
    assert any(t["id"] == ticket_id for t in tickets)

    # Delete ticket
    del_res = client.delete(f"/api/tickets/{ticket_id}")
    assert del_res.status_code == 200, del_res.text
    assert del_res.json() == {"deleted": ticket_id}


def test_session_and_message_flow():
    # Login user
    client.post(
        "/api/auth/login",
        json={"email": "ticketuser@example.com", "password": "Password123!"},
    )

    # Start session
    session_res = client.post("/api/session/start")
    assert session_res.status_code == 200, session_res.text
    session_id = session_res.json()["session_id"]

    # Send message
    msg_res = client.post("/api/message", json={"session_id": session_id, "text": "Hello IT support"})
    assert msg_res.status_code == 200, msg_res.text
    msg_data = msg_res.json()
    assert msg_data["role"] == "agent"
    assert "text" in msg_data


def test_health_and_html_endpoints():
    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    index_res = client.get("/")
    assert index_res.status_code == 200

    main_res = client.get("/main")
    assert main_res.status_code == 200

    login_res = client.get("/login")
    assert login_res.status_code == 200

    login_html_res = client.get("/login.html")
    assert login_html_res.status_code == 200


def test_vision_service_upload():
    # Login user
    client.post(
        "/api/auth/login",
        json={"email": "ticketuser@example.com", "password": "Password123!"},
    )

    # Upload test PDF/file
    file_content = b"%PDF-1.4 Mock PDF content for test"
    files = {"file": ("test_doc.pdf", file_content, "application/pdf")}
    data = {"prompt": "Analyze this policy document"}

    res = client.post("/api/vision/analyze", files=files, data=data)
    assert res.status_code == 200, res.text
    json_data = res.json()
    assert json_data["filename"] == "test_doc.pdf"
    assert "answer" in json_data


