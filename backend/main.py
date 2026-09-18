"""
Voice IT Helpdesk — Backend (Person 5)

This is the FastAPI skeleton for Week 1. Every endpoint currently
returns mock data so the frontend can be built against a stable
contract. As Person 1 (voice), Person 2 (agent), Person 3 (MCP tools)
and Person 4 (RAG) finish their pieces, swap the mock logic inside
each endpoint for real calls — the request/response shapes below are
the contract they should build against too.
"""

import itertools
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Voice IT Helpdesk API")

# Allow the frontend (served separately, e.g. via a static file server
# or Live Server) to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this before deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory "database" (mock only — replace with real persistence later)
# ---------------------------------------------------------------------------

SESSIONS: dict = {}

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

MOCK_USER = {"name": "Arpita", "initials": "AR", "role": "Student"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MessageIn(BaseModel):
    session_id: str
    text: str


class MessageOut(BaseModel):
    role: str  # "user" | "agent"
    text: str
    timestamp: str


class TicketIn(BaseModel):
    issue: str


class Ticket(BaseModel):
    id: str
    issue: str
    status: str
    opened: str


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.post("/api/session/start")
def start_session():
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {"created_at": datetime.utcnow().isoformat(), "history": []}
    return {"session_id": session_id}


# ---------------------------------------------------------------------------
# Conversation endpoint
# ---------------------------------------------------------------------------

@app.post("/api/message", response_model=MessageOut)
def send_message(payload: MessageIn):
    if payload.session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    # --- MOCK RESPONSE ---
    # Replace this block with a call to Person 2's Foundry Agent once
    # it's ready. For now we fake a canned IT-support style reply.
    reply_text = (
        "I'll check your device status now. "
        "Your wifi is connected but DNS is failing. I've opened a ticket for this."
    )

    SESSIONS[payload.session_id]["history"].append(
        {"role": "user", "text": payload.text}
    )
    SESSIONS[payload.session_id]["history"].append(
        {"role": "agent", "text": reply_text}
    )

    return MessageOut(role="agent", text=reply_text, timestamp=datetime.utcnow().isoformat())


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
# Auth endpoints (mock — replace with real auth in Week 3)
# ---------------------------------------------------------------------------

@app.get("/api/auth/me")
def get_current_user():
    return MOCK_USER


@app.post("/api/auth/logout")
def logout():
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# Health check (useful for Azure + Application Insights later)
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
