import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

# ============================================================
# IMPORT FROM AUTH.PY
# Adjust 'backend.auth' to 'auth' if auth.py is in the same folder as main.py
# ============================================================
try:
    from backend.auth import (
        oauth, AUTH_SESSIONS, ensure_schema, get_db_connection,
        utc_now_iso, get_current_customer, create_session, 
        serialize_customer, router as auth_router, TENANT_ID, CLIENT_ID
    )
except ImportError:
    from auth import (
        oauth, AUTH_SESSIONS, ensure_schema, get_db_connection,
        utc_now_iso, get_current_customer, create_session, 
        serialize_customer, router as auth_router, TENANT_ID, CLIENT_ID
    )

# ============================================================
# ENV & PATHS
# ============================================================
load_dotenv()

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

if not os.path.exists(FRONTEND_DIR):
    FRONTEND_DIR = BACKEND_DIR

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BACKEND_DIR, "customer_auth.db"))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-please-32chars-min")

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="Voice IT Helpdesk API")

# SessionMiddleware is REQUIRED for authlib OAuth state
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000,http://localhost:3000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True, # CRITICAL: Allows frontend to send/receive cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the auth routes from auth.py (/api/auth/...)
app.include_router(auth_router)

# ============================================================
# DATABASE INIT
# ============================================================
def init_db() -> None:
    ensure_schema()
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                issue TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()

init_db()

# ============================================================
# RUNTIME STORAGE
# ============================================================
CONVERSATION_SESSIONS: Dict[str, Dict[str, Any]] = {}

# ============================================================
# PYDANTIC SCHEMAS
# ============================================================
class MessageIn(BaseModel):
    session_id: str
    text: str

class MessageOut(BaseModel):
    role: str
    text: str
    timestamp: str

class TicketIn(BaseModel):
    issue: str

class Ticket(BaseModel):
    id: str
    issue: str
    status: str
    opened: str

class CustomerRegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str
    email: EmailStr
    password: str
    confirm_password: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Full name must be at least 2 characters long.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must include at least one uppercase letter.")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must include at least one lowercase letter.")
        if not re.search(r"\d", value):
            raise ValueError("Password must include at least one number.")
        if not re.search(r"[^A-Za-z0-9]", value):
            raise ValueError("Password must include at least one special character.")
        return value

    @model_validator(mode="after")
    def validate_confirm_password(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self

class CustomerLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str

# ============================================================
# LOCAL AUTH HELPERS (Email/Password)
# ============================================================
def normalize_email(email: str) -> str:
    return email.strip().lower()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or not stored_hash.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, salt, stored_digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    candidate_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    ).hex()
    return hmac.compare_digest(candidate_digest, stored_digest)

@app.post("/api/auth/register", status_code=201)
def register_customer(payload: CustomerRegisterIn):
    email = normalize_email(str(payload.email))
    with get_db_connection() as conn:
        existing = conn.execute("SELECT 1 FROM customers WHERE email =?", (email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        customer_id = uuid.uuid4().hex
        now = utc_now_iso()
        password_hash = hash_password(payload.password)
        conn.execute(
            """
            INSERT INTO customers (id, full_name, email, password_hash, role, auth_provider, created_at, updated_at)
            VALUES (?,?,?,?, 'customer','local',?,?)
            """,
            (customer_id, payload.full_name.strip(), email, password_hash, now, now),
        )
        conn.commit()
    return serialize_customer(conn.execute("SELECT * FROM customers WHERE id =?", (customer_id,)).fetchone())

@app.post("/api/auth/login")
def login_customer(payload: CustomerLoginIn, response: Response):
    email = normalize_email(str(payload.email))
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE email =?", (email,)).fetchone()
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    create_session(response, row["id"], provider=row["auth_provider"])
    return serialize_customer(row)

# ============================================================
# CONVERSATION & TICKETS
# ============================================================
@app.post("/api/session/start")
def start_session(request: Request):
    customer = get_current_customer(request)
    session_id = str(uuid.uuid4())
    CONVERSATION_SESSIONS[session_id] = {
        "created_at": utc_now_iso(),
        "user_id": customer["id"],
        "history": [],
    }
    return {"session_id": session_id}

@app.post("/api/message", response_model=MessageOut)
def send_message(payload: MessageIn, request: Request):
    customer = get_current_customer(request)
    conversation = CONVERSATION_SESSIONS.get(payload.session_id)
    if conversation is None or conversation["user_id"] != customer["id"]:
        raise HTTPException(status_code=404, detail="Session not found")
    
    reply_text = "I'll check your device status now. Your wifi is connected but DNS is failing. I've opened a ticket for this."
    
    conversation["history"].append({"role": "user", "text": payload.text})
    conversation["history"].append({"role": "agent", "text": reply_text})
    return MessageOut(role="agent", text=reply_text, timestamp=utc_now_iso())

def serialize_ticket(row: sqlite3.Row) -> Dict[str, str]:
    return {
        "id": row["id"],
        "issue": row["issue"],
        "status": row["status"],
        "opened": row["created_at"],
    }

@app.get("/api/tickets", response_model=List[Ticket])
def get_tickets(request: Request):
    customer = get_current_customer(request)
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, issue, status, created_at FROM tickets WHERE customer_id = ? ORDER BY created_at DESC",
            (customer["id"],),
        ).fetchall()
    return [serialize_ticket(row) for row in rows]

@app.post("/api/tickets", response_model=Ticket)
def create_ticket(payload: TicketIn, request: Request):
    customer = get_current_customer(request)
    issue = payload.issue.strip()
    if not issue:
        raise HTTPException(status_code=422, detail="Issue description is required.")
    ticket_id = uuid.uuid4().hex[:8]
    created_at = utc_now_iso()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO tickets (id, customer_id, issue, status, created_at) VALUES (?, ?, ?, 'open', ?)",
            (ticket_id, customer["id"], issue, created_at),
        )
        conn.commit()
    return {"id": ticket_id, "issue": issue, "status": "open", "opened": created_at}

@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: str, request: Request):
    customer = get_current_customer(request)
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM tickets WHERE id = ? AND customer_id = ?",
            (ticket_id, customer["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        conn.execute("DELETE FROM tickets WHERE id = ? AND customer_id = ?", (ticket_id, customer["id"]))
        conn.commit()
    return {"deleted": ticket_id}

@app.get("/api/health")
def health():
    return {"status": "ok", "time": utc_now_iso(), "entra_configured": bool(TENANT_ID and CLIENT_ID)}

# ============================================================
# FRONTEND SERVING
# ============================================================
@app.get("/", response_class=HTMLResponse)
def serve_index():
    # Serve index.html as the initial landing page
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    
    alt_path = os.path.join(BACKEND_DIR, "index.html")
    if os.path.exists(alt_path):
        with open(alt_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
            
    return HTMLResponse(content="<h1>Voice IT Helpdesk</h1><p>index.html not found.</p>", status_code=200)

@app.get("/main", response_class=HTMLResponse)
@app.get("/main.html", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def serve_main():
    # Serve main.html as the dashboard (after login)
    for name in ["main.html"]:
        main_path = os.path.join(FRONTEND_DIR, name)
        if os.path.exists(main_path):
            with open(main_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        alt_path = os.path.join(BACKEND_DIR, name)
        if os.path.exists(alt_path):
            with open(alt_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>main.html not found.</h1>", status_code=404)

@app.get("/login", response_class=HTMLResponse)
@app.get("/Login.html", response_class=HTMLResponse)
@app.get("/login.html", response_class=HTMLResponse)
def serve_login():
    for name in ["Login.html", "login.html"]:
        login_path = os.path.join(FRONTEND_DIR, name)
        if os.path.exists(login_path):
            with open(login_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        alt_path = os.path.join(BACKEND_DIR, name)
        if os.path.exists(alt_path):
            with open(alt_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Login file not found.</h1>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)