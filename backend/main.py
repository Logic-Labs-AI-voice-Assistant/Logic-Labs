import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

# ============================================================
# ENV & PATHS
# ============================================================
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Support both layouts: frontend/ folder or same folder as backend
if not os.path.exists(FRONTEND_DIR):
    FRONTEND_DIR = BACKEND_DIR

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BACKEND_DIR, "customer_auth.db"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-please-32chars-min")

# Entra ID Config
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/api/auth/microsoft/callback")

if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"
if SESSION_COOKIE_SAMESITE == "none" and not SESSION_COOKIE_SECURE:
    SESSION_COOKIE_SECURE = True

# ============================================================
# AUTH IMPORT (Entra ID)
# ============================================================
from backend.auth import oauth, AUTH_SESSIONS, ensure_schema

# ============================================================
# RUNTIME STORAGE
# ============================================================
CONVERSATION_SESSIONS: Dict[str, Dict[str, Any]] = {}

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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# DATABASE
# ============================================================
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
# UTILS
# ============================================================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

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

def serialize_customer(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "role": row["role"],
        "auth_provider": row["auth_provider"] if "auth_provider" in row.keys() else "local",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def create_local_session(response: Response, user_id: str, provider: str = "local"):
    session_id = secrets.token_urlsafe(32)
    AUTH_SESSIONS[session_id] = {
        "user_id": user_id,
        "provider": provider,
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
    return session_id

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
            "SELECT id, full_name, email, role, auth_provider, azure_oid, created_at, updated_at FROM customers WHERE id =?",
            (user_id,),
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

def serialize_ticket(row: sqlite3.Row) -> Dict[str, str]:
    return {
        "id": row["id"],
        "issue": row["issue"],
        "status": row["status"],
        "opened": row["created_at"],
    }

def get_customer_ticket(ticket_id: str, customer_id: str) -> sqlite3.Row:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id, issue, status, created_at FROM tickets WHERE id = ? AND customer_id = ?",
            (ticket_id, customer_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return row

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
    get_customer_ticket(ticket_id, customer["id"])
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM tickets WHERE id = ? AND customer_id = ?",
            (ticket_id, customer["id"]),
        )
        conn.commit()
    return {"deleted": ticket_id}

# ============================================================
# AUTH - LOCAL (Email/Password)
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
            INSERT INTO customers (id, full_name, email, password_hash, role, auth_provider, created_at, updated_at)
            VALUES (?,?,?,?, 'customer','local',?,?)
            """,
            (customer_id, payload.full_name.strip(), email, password_hash, now, now),
        )
        conn.commit()
    return {"id": customer_id, "full_name": payload.full_name.strip(), "email": email, "role": "customer", "auth_provider": "local", "created_at": now, "updated_at": now}

@app.post("/api/auth/login")
def login_customer(payload: CustomerLoginIn, response: Response):
    email = normalize_email(str(payload.email))
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, full_name, email, password_hash, role, auth_provider, azure_oid, created_at, updated_at FROM customers WHERE email =?", (email,)).fetchone()
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    create_local_session(response, row["id"], provider=row["auth_provider"])
    return serialize_customer(row)

# ============================================================
# AUTH - MICROSOFT ENTRA ID
# ============================================================
@app.get("/api/auth/microsoft/login")
async def microsoft_login(request: Request):
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Entra ID not configured in .env - set TENANT_ID, CLIENT_ID, CLIENT_SECRET")
    return await oauth.azure.authorize_redirect(request, REDIRECT_URI)

@app.get("/api/auth/microsoft/callback")
async def microsoft_callback(request: Request, response: Response):
    try:
        token = await oauth.azure.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Microsoft auth failed: {str(e)}")
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = token  # fallback

    oid = userinfo.get("oid") or userinfo.get("sub")
    email = (userinfo.get("email") or userinfo.get("preferred_username") or "").lower().strip()
    full_name = userinfo.get("name") or email.split("@")[0]

    if not oid or not email:
        raise HTTPException(status_code=400, detail="Microsoft did not return oid/email")

    now = utc_now_iso()
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE azure_oid = ?", (oid,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
            if row:
                # Link existing account
                conn.execute("UPDATE customers SET azure_oid = ?, auth_provider = ?, updated_at = ? WHERE id = ?", (oid, "microsoft", now, row["id"]))
                conn.commit()
                row = conn.execute("SELECT * FROM customers WHERE id = ?", (row["id"],)).fetchone()
        if not row:
            customer_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO customers (id, full_name, email, password_hash, role, auth_provider, azure_oid, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?, ?,?)""",
                (customer_id, full_name, email, "", "customer", "microsoft", oid, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

    create_local_session(response, row["id"], provider="microsoft")
    # Redirect to app home - cookie is now set, frontend will detect logged in user
    return RedirectResponse(url="/", headers=response.headers)

@app.get("/api/auth/me")
def get_me(request: Request):
    row = get_current_customer(request)
    return serialize_customer(row)

@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        AUTH_SESSIONS.pop(session_id, None)
    response.delete_cookie(key="session_id", path="/", samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE)
    return {"status": "logged_out"}

@app.get("/api/health")
def health():
    return {"status": "ok", "time": utc_now_iso(), "entra_configured": bool(TENANT_ID and CLIENT_ID)}

# ============================================================
# FRONTEND SERVING - PUBLIC, NO AUTH REQUIRED
# ============================================================
@app.get("/", response_class=HTMLResponse)
def serve_index():
    """PUBLIC - Anyone can access index.html"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    # Fallback: if frontend folder missing, check backend dir
    alt_path = os.path.join(BACKEND_DIR, "index.html")
    if os.path.exists(alt_path):
        with open(alt_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Voice IT Helpdesk</h1><p>Frontend index.html not found. Go to /login</p><a href='/login'>Login</a>", status_code=200)

@app.get("/login", response_class=HTMLResponse)
def serve_login():
    """PUBLIC - Anyone can access login page"""
    for name in ["Login.html", "login.html"]:
        login_path = os.path.join(FRONTEND_DIR, name)
        if os.path.exists(login_path):
            with open(login_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        alt_path = os.path.join(BACKEND_DIR, name)
        if os.path.exists(alt_path):
            with open(alt_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Login file not found. Please ensure Login.html is in frontend directory.</h1>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
