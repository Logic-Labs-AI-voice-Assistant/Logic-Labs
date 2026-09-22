import os
import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

load_dotenv()

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

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BACKEND_DIR, "customer_auth.db"))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-please-32chars-min")

# Title updated to remove "Auto Detect"
app = FastAPI(title="Voice IT Helpdesk")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
allowed_origins = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000,http://localhost:3000"
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)

# --- Azure Services ---
speech_router = None
vision_router = None
agent_loaded = False
call_jarvis_vision = None
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
AGENT_ID = os.getenv("AGENT_ID")

try:
    from backend.speech_service import router as speech_router
    print("✓ Loaded backend.speech_service")
except ImportError:
    try:
        from speech_service import router as speech_router
        print("✓ Loaded speech_service")
    except Exception as e:
        print(f"✗ speech_service not loaded: {e}")
        speech_router = None

try:
    from backend.agent_service import call_jarvis_vision as _call_jarvis
    from backend.agent_service import PROJECT_ENDPOINT as _ep, AGENT_ID as _aid
    call_jarvis_vision = _call_jarvis
    PROJECT_ENDPOINT = _ep
    AGENT_ID = _aid
    agent_loaded = True
    print(f"✓ Loaded agent_service -> {PROJECT_ENDPOINT} Agent: {AGENT_ID}")
except ImportError:
    try:
        from agent_service import call_jarvis_vision as _call_jarvis
        from agent_service import PROJECT_ENDPOINT as _ep, AGENT_ID as _aid
        call_jarvis_vision = _call_jarvis
        PROJECT_ENDPOINT = _ep
        AGENT_ID = _aid
        agent_loaded = True
        print("✓ Loaded agent_service locally")
    except Exception as e:
        print(f"✗ agent_service not loaded: {e}")
        agent_loaded = False

try:
    from backend.vision_service import router as vision_router
    print("✓ Loaded vision_service (EasyOCR + JarvisVision)")
except ImportError:
    try:
        from vision_service import router as vision_router
        print("✓ Loaded vision_service locally")
    except Exception as e:
        print(f"✗ vision_service not loaded: {e}")
        vision_router = None

if speech_router:
    app.include_router(speech_router)
if vision_router:
    app.include_router(vision_router)

# Fallback if agent not loaded
if not agent_loaded:
    def call_jarvis_vision(user_text: str, history: List[Dict] = None, customer: Optional[Dict] = None, thread_id: Optional[str] = None):
        return {
            "answer": "Agent not configured. Set PROJECT_ENDPOINT, PROJECT_API_KEY, AGENT_ID in backend/.env",
            "thread_id": thread_id
        }

def init_db() -> None:
    ensure_schema()
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                issue TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE CASCADE
            )
        """)
        conn.commit()

init_db()

CONVERSATION_SESSIONS: Dict[str, Dict[str, Any]] = {}

class MessageIn(BaseModel):
    session_id: str
    text: str

class MessageOut(BaseModel):
    role: str
    text: str
    timestamp: str
    thread_id: Optional[str] = None

class TicketIn(BaseModel):
    issue: str
    description: str = ""
    category: str = "general"
    priority: str = "medium"

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
        existing = conn.execute("SELECT 1 FROM customers WHERE email = ?", (email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        customer_id = uuid.uuid4().hex
        now = utc_now_iso()
        password_hash = hash_password(payload.password)
        conn.execute(
            """INSERT INTO customers (id, full_name, email, password_hash, role, auth_provider, created_at, updated_at)
               VALUES (?,?,?,?, 'customer','local',?,?)""",
            (customer_id, payload.full_name.strip(), email, password_hash, now, now),
        )
        conn.commit()
    with get_db_connection() as conn:
        return serialize_customer(conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())

@app.post("/api/auth/login")
def login_customer(payload: CustomerLoginIn, response: Response):
    email = normalize_email(str(payload.email))
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    create_session(response, row["id"], provider=row["auth_provider"])
    return serialize_customer(row)

@app.post("/api/session/start")
def start_session(request: Request):
    customer = get_current_customer(request)
    session_id = str(uuid.uuid4())
    CONVERSATION_SESSIONS[session_id] = {
        "created_at": utc_now_iso(),
        "user_id": customer["id"],
        "history": [],
        "thread_id": None,
    }
    return {"session_id": session_id}

@app.post("/api/message", response_model=MessageOut)
def send_message(payload: MessageIn, request: Request):
    customer = get_current_customer(request)
    conversation = CONVERSATION_SESSIONS.get(payload.session_id)
    if conversation is None or conversation["user_id"] != customer["id"]:
        raise HTTPException(status_code=404, detail="Session not found")

    result = call_jarvis_vision(
        user_text=payload.text,
        history=conversation["history"],
        customer=customer,
        thread_id=conversation.get("thread_id")
    )
    
    if isinstance(result, dict):
        reply_text = result.get("answer", "")
        thread_id = result.get("thread_id")
    else:
        reply_text = str(result)
        thread_id = conversation.get("thread_id")

    conversation["history"].append({"role": "user", "text": payload.text})
    conversation["history"].append({"role": "agent", "text": reply_text})
    conversation["thread_id"] = thread_id

    return MessageOut(role="agent", text=reply_text, timestamp=utc_now_iso(), thread_id=thread_id)

def serialize_ticket(row: sqlite3.Row) -> Dict[str, str]:
    return {"id": row["id"], "issue": row["issue"], "status": row["status"], "opened": row["created_at"]}

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
        row = conn.execute("SELECT 1 FROM tickets WHERE id = ? AND customer_id = ?", (ticket_id, customer["id"])).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        conn.execute("DELETE FROM tickets WHERE id = ? AND customer_id = ?", (ticket_id, customer["id"]))
        conn.commit()
    return {"deleted": ticket_id}

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "time": utc_now_iso(),
        "entra_configured": bool(TENANT_ID and CLIENT_ID),
        "speech_loaded": speech_router is not None,
        "vision_loaded": vision_router is not None,
        "agent_loaded": agent_loaded,
        "agent_id": AGENT_ID,
        "project_endpoint": PROJECT_ENDPOINT,
    }

def _read_html(name: str):
    for base in [FRONTEND_DIR, BACKEND_DIR]:
        p = os.path.join(base, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return None

@app.get("/", response_class=HTMLResponse)
def serve_index():
    html = _read_html("index.html")
    return HTMLResponse(content=html or "<h1>index.html not found</h1>", status_code=200 if html else 404)

@app.get("/main", response_class=HTMLResponse)
@app.get("/main.html", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def serve_main():
    html = _read_html("main.html")
    return HTMLResponse(content=html or "<h1>main.html not found</h1>", status_code=200 if html else 404)

@app.get("/login", response_class=HTMLResponse)
@app.get("/login.html", response_class=HTMLResponse)
@app.get("/Login.html", response_class=HTMLResponse)
def serve_login():
    html = _read_html("Login.html") or _read_html("login.html")
    return HTMLResponse(content=html or "<h1>Login.html not found</h1>", status_code=200 if html else 404)