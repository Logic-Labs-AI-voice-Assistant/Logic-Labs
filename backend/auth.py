"""
auth.py - Microsoft Entra ID + Local Auth integration
for FastAPI + HTML + SQLite project

Install: pip install authlib httpx python-dotenv

.env required:
TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CLIENT_SECRET=your-client-secret-value
REDIRECT_URI=http://localhost:8000/api/auth/microsoft/callback
SECRET_KEY=any-random-32-char-string
SESSION_TTL_SECONDS=3600
"""
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

from dotenv import load_dotenv
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth

# ============================================================
# ENV
# ============================================================
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/api/auth/microsoft/callback")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()

if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"
if SESSION_COOKIE_SAMESITE == "none" and not SESSION_COOKIE_SECURE:
    SESSION_COOKIE_SECURE = True

# Reuse paths from main.py logic
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BACKEND_DIR, "customer_auth.db"))

# ============================================================
# IN-MEMORY SESSION STORE - Shared with main.py
# ============================================================
# session_id -> {user_id, expires_at, provider}
AUTH_SESSIONS: Dict[str, Dict[str, Any]] = {}

# ============================================================
# OAUTH SETUP
# ============================================================
oauth = OAuth()
oauth.register(
    name="azure",
    server_metadata_url=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0/.well-known/openid-configuration",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    client_kwargs={
        "scope": "openid email profile",
        "prompt": "select_account",
    },
)

# ============================================================
# DB HELPERS
# ============================================================
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def utc_now():
    return datetime.now(timezone.utc)

def utc_now_iso():
    return utc_now().isoformat()

def ensure_schema():
    """Adds Entra ID columns to existing customers table without breaking local users"""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'customer',
                auth_provider TEXT NOT NULL DEFAULT 'local',
                azure_oid TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Migration for existing DB: add columns if not exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(customers)").fetchall()]
        if "auth_provider" not in cols:
            conn.execute("ALTER TABLE customers ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
        if "azure_oid" not in cols:
            conn.execute("ALTER TABLE customers ADD COLUMN azure_oid TEXT")
        # make password_hash nullable for SSO users (keep empty string for them)
        conn.commit()

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

def create_session(response: Response, user_id: str, provider: str = "local") -> str:
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
# ROUTER - Entra ID Endpoints
# ============================================================
router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.get("/microsoft/login")
async def microsoft_login(request: Request):
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Entra ID not configured in .env")
    # authlib needs session middleware to store state, but we use our own dict
    # So we manually store state in cookie via starlette session - workaround: use request.session if available
    # If you didn't add SessionMiddleware, this still works, authlib will handle state via its own cookie
    return await oauth.azure.authorize_redirect(request, REDIRECT_URI)

@router.get("/microsoft/callback")
async def microsoft_callback(request: Request, response: Response):
    try:
        token = await oauth.azure.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Microsoft auth failed: {str(e)}")
    
    userinfo = token.get("userinfo")
    if not userinfo:
        # fallback: parse id_token
        userinfo = token

    # Claims from Entra ID v2.0
    oid = userinfo.get("oid") or userinfo.get("sub")
    email = (userinfo.get("email") or userinfo.get("preferred_username") or "").lower().strip()
    full_name = userinfo.get("name") or email.split("@")[0]

    if not oid or not email:
        raise HTTPException(status_code=400, detail="Microsoft did not return email/oid")

    ensure_schema()
    now = utc_now_iso()

    with get_db_connection() as conn:
        # 1. Try find by azure_oid
        row = conn.execute("SELECT * FROM customers WHERE azure_oid = ?", (oid,)).fetchone()
        # 2. If not found, try link by email (if local account exists, convert to hybrid)
        if not row:
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
            if row:
                # Link existing local account to Entra ID
                conn.execute(
                    "UPDATE customers SET azure_oid = ?, auth_provider = ?, updated_at = ? WHERE id = ?",
                    (oid, "microsoft" if row["auth_provider"] == "local" else row["auth_provider"], now, row["id"]),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM customers WHERE id = ?", (row["id"],)).fetchone()
        
        # 3. If still not found, create new customer
        if not row:
            customer_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO customers (id, full_name, email, password_hash, role, auth_provider, azure_oid, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?, ?,?)
                """,
                (customer_id, full_name, email, "", "customer", "microsoft", oid, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

    # Create local session (same as your existing password login)
    create_session(response, row["id"], provider="microsoft")
    
    # Redirect to frontend app - user will be authenticated via cookie
    # If your frontend is SPA, redirect to / or /login?success=1
    return RedirectResponse(url="/", headers=response.headers)

@router.get("/me")
def get_me(request: Request):
    row = get_current_customer(request)
    return serialize_customer(row)

@router.post("/logout")
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
    return {"status": "logged_out", "microsoft_logout_url": f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout?post_logout_redirect_uri=http://localhost:8000/login" if TENANT_ID else None}

# Dependency for protected routes
def require_auth(request: Request):
    return get_current_customer(request)
