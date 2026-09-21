import hashlib
import hmac
import itertools
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    field_validator,
    model_validator,
)

# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# ============================================================
# CONFIGURATION
# ============================================================
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(BACKEND_DIR, "customer_auth.db")
)
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()

if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"

if SESSION_COOKIE_SAMESITE == "none" and not SESSION_COOKIE_SECURE:
    SESSION_COOKIE_SECURE = True

# ============================================================
# IN-MEMORY STORAGE
# ============================================================
CONVERSATION_SESSIONS: Dict[str, Dict[str, Any]] = {}
AUTH_SESSIONS: Dict[str, Dict[str, Any]] = {} # session_id -> {user_id, expires_at}

TICKETS: Dict[str, Dict[str, str]] = {
    "4821": {"id": "4821", "issue": "DNS failure", "status": "in_progress", "opened": "Today"},
    "4802": {"id": "4802", "issue": "VPN timeout", "status": "open", "opened": "2 days ago"},
    "4790": {"id": "4790", "issue": "Password reset", "status": "resolved", "opened": "5 days ago"},
}
_ticket_id_counter = itertools.count(4900)

# ============================================================
# FASTAPI APPLICATION
# ============================================================
app = FastAPI(title="Voice IT Helpdesk API")

# ============================================================
# CORS - MUST be before routes
# ============================================================
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# DATABASE FUNCTIONS
# ============================================================
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'customer',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

init_db()

# ============================================================
# UTILITY FUNCTIONS
# ============================================================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

def normalize_email(email: str) -> str:
    return email.strip().lower()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return f"pbkdf2_sha256${salt}${digest.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, salt, stored_digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    candidate_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    ).hex()
    return hmac.compare_digest(candidate_digest, stored_digest)

def serialize_customer(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def get_current_customer(request: Request) -> sqlite3.Row:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    session = AUTH_SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")

    if session["expires_at"] <= utc_now().timestamp():
        AUTH_SESSIONS.pop(session_id, None)
        raise HTTPException(status_code=401, detail="Session expired.")

    user_id = session["user_id"]
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, full_name, email, role, created_at, updated_at FROM customers WHERE id =?",
            (user_id,)
        ).fetchone()

    if row is None:
        AUTH_SESSIONS.pop(session_id, None)
        raise HTTPException(status_code=401, detail="Authentication required.")
    return row

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
# CONVERSATION SESSION ENDPOINTS
# ============================================================
@app.post("/api/session/start")
def start_session():
    session_id = str(uuid.uuid4())
    CONVERSATION_SESSIONS[session_id] = {
        "created_at": utc_now_iso(),
        "history": []
    }
    return {"session_id": session_id}

# ============================================================
# CONVERSATION ENDPOINT
# ============================================================
@app.post("/api/message", response_model=MessageOut)
def send_message(payload: MessageIn):
    if payload.session_id not in CONVERSATION_SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    reply_text = (
        "I'll check your device status now. "
        "Your wifi is connected but DNS is failing. "
        "I've opened a ticket for this."
    )

    CONVERSATION_SESSIONS[payload.session_id]["history"].append(
        {"role": "user", "text": payload.text}
    )
    CONVERSATION_SESSIONS[payload.session_id]["history"].append(
        {"role": "agent", "text": reply_text}
    )

    return MessageOut(role="agent", text=reply_text, timestamp=utc_now_iso())

# ============================================================
# TICKET ENDPOINTS
# ============================================================
@app.get("/api/tickets", response_model=List[Ticket])
def get_tickets():
    return list(TICKETS.values())

@app.post("/api/tickets", response_model=Ticket)
def create_ticket(payload: TicketIn):
    new_id = str(next(_ticket_id_counter))
    issue = payload.issue.strip() or "New ticket"
    ticket = {"id": new_id, "issue": issue, "status": "open", "opened": "Just now"}
    TICKETS[new_id] = ticket
    return ticket

@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: str):
    if ticket_id not in TICKETS:
        raise HTTPException(status_code=404, detail="Ticket not found")
    del TICKETS[ticket_id]
    return {"deleted": ticket_id}

# ============================================================
# CUSTOMER AUTHENTICATION ENDPOINTS
# ============================================================
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
            INSERT INTO customers (id, full_name, email, password_hash, role, created_at, updated_at)
            VALUES (?,?,?,?, 'customer',?,?)
            """,
            (customer_id, payload.full_name.strip(), email, password_hash, now, now),
        )
        conn.commit()

    return {
        "id": customer_id,
        "full_name": payload.full_name.strip(),
        "email": email,
        "role": "customer",
        "created_at": now,
        "updated_at": now
    }

@app.post("/api/auth/login")
def login_customer(payload: CustomerLoginIn, response: Response):
    email = normalize_email(str(payload.email))
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, full_name, email, password_hash, role, created_at, updated_at FROM customers WHERE email =?",
            (email,),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    session_id = secrets.token_urlsafe(32)
    AUTH_SESSIONS[session_id] = {
        "user_id": row["id"],
        "expires_at": utc_now().timestamp() + SESSION_TTL_SECONDS,
    }

    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )
    return serialize_customer(row)

@app.get("/api/auth/me")
def get_current_user(request: Request):
    row = get_current_customer(request)
    return serialize_customer(row)

@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        AUTH_SESSIONS.pop(session_id, None)

    response.delete_cookie(
        key="session_id",
        path="/",
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
    )
    return {"status": "logged_out"}

# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "time": utc_now_iso()}

# ============================================================
# FRONTEND SERVING - MUST BE LAST
# ============================================================
@app.get("/", response_class=HTMLResponse)
def serve_index():
    """
    Serves the main index page. 
    No authentication is required to access this route.
    """
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(
        content="<h1>Frontend file not found. Please ensure index.html is in the frontend directory.</h1>", 
        status_code=404
    )

@app.get("/login", response_class=HTMLResponse)
def serve_login():
    """
    Serves the combined login page. 
    No authentication is required to access this route.
    """
    # Note: Ensure the file is named exactly 'login.html' (lowercase)
    login_path = os.path.join(FRONTEND_DIR, "Login.html")
    if os.path.exists(login_path):
        with open(login_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(
        content="<h1>Login file not found. Please ensure login.html is in the frontend directory.</h1>", 
        status_code=404
    )
# ============================================================
# RUN SERVER
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)