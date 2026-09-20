"""
Voice IT Helpdesk — Backend (Person 5)

This API currently supports the ticket workflow plus a real customer auth layer
that is stored in SQLite and persists across browser refreshes via secure HTTP-only
cookies.
"""

import hashlib
import itertools
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI(title="Voice IT Helpdesk API")

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000",
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

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "customer_auth.db"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax")

CONVERSATION_SESSIONS: dict[str, dict[str, Any]] = {}
AUTH_SESSIONS: dict[str, str] = {}

TICKETS: dict = {
    "4821": {
        "id": "4821",
        "issue": "DNS failure",
        "status": "in_progress",
        "opened": "Today",
    },
    "4802": {
        "id": "4802",
        "issue": "VPN timeout",
        "status": "open",
        "opened": "2 days ago",
    },
    "4790": {
        "id": "4790",
        "issue": "Password reset",
        "status": "resolved",
        "opened": "5 days ago",
    },
}
_ticket_id_counter = itertools.count(4900)


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


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, salt, digest = stored_hash.split("$", 2)
    except ValueError:
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return secrets.compare_digest(candidate, digest)


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

    user_id = AUTH_SESSIONS.get(session_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, full_name, email, role, created_at, updated_at FROM customers WHERE id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        AUTH_SESSIONS.pop(session_id, None)
        raise HTTPException(status_code=401, detail="Authentication required.")

    return row


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


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.post("/api/session/start")
def start_session():
    session_id = str(uuid.uuid4())
    CONVERSATION_SESSIONS[session_id] = {"created_at": datetime.now(timezone.utc).isoformat(), "history": []}
    return {"session_id": session_id}


# ---------------------------------------------------------------------------
# Conversation endpoint
# ---------------------------------------------------------------------------

@app.post("/api/message", response_model=MessageOut)
def send_message(payload: MessageIn):
    if payload.session_id not in CONVERSATION_SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    reply_text = (
        "I'll check your device status now. "
        "Your wifi is connected but DNS is failing. I've opened a ticket for this."
    )

    CONVERSATION_SESSIONS[payload.session_id]["history"].append({"role": "user", "text": payload.text})
    CONVERSATION_SESSIONS[payload.session_id]["history"].append({"role": "agent", "text": reply_text})

    return MessageOut(role="agent", text=reply_text, timestamp=datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Ticket endpoints
# ---------------------------------------------------------------------------

@app.get("/api/tickets", response_model=List[Ticket])
def get_tickets():
    return list(TICKETS.values())


@app.post("/api/tickets", response_model=Ticket)
def create_ticket(payload: TicketIn):
    new_id = str(next(_ticket_id_counter))
    ticket = {
        "id": new_id,
        "issue": payload.issue or "New ticket",
        "status": "open",
        "opened": "Just now",
    }
    TICKETS[new_id] = ticket
    return ticket


@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: str):
    if ticket_id not in TICKETS:
        raise HTTPException(status_code=404, detail="Ticket not found")
    del TICKETS[ticket_id]
    return {"deleted": ticket_id}


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/register", status_code=201)
def register_customer(payload: CustomerRegisterIn):
    email = normalize_email(str(payload.email))

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM customers WHERE email = ?",
            (email,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        customer_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        password_hash = hash_password(payload.password)

        conn.execute(
            """
            INSERT INTO customers (id, full_name, email, password_hash, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'customer', ?, ?)
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
        "updated_at": now,
    }


@app.post("/api/auth/login")
def login_customer(payload: CustomerLoginIn, response: Response):
    email = normalize_email(str(payload.email))

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, full_name, email, password_hash, role, created_at, updated_at FROM customers WHERE email = ?",
            (email,),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    session_id = uuid.uuid4().hex
    AUTH_SESSIONS[session_id] = row["id"]

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
        "session_id",
        path="/",
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
    )
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
