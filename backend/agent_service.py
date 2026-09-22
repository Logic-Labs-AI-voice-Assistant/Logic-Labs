import os
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder
from azure.identity import DefaultAzureCredential, ClientSecretCredential

PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
AGENT_ID = os.getenv("AGENT_ID")

_client = None

def get_agent_client() -> AgentsClient:
    global _client
    if _client is None:
        if not PROJECT_ENDPOINT:
            raise ValueError("Missing required environment variable: PROJECT_ENDPOINT")
        
        try:
            credential = DefaultAzureCredential()
        except Exception:
            credential = ClientSecretCredential(
                tenant_id=TENANT_ID,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET
            )
        
        _client = AgentsClient(
            endpoint=PROJECT_ENDPOINT,
            credential=credential,
            credential_scopes=["https://ai.azure.com/.default"]
        )
    return _client

def call_jarvis_vision(user_text: str, history: List[Dict] = None, customer: Optional[Dict] = None, thread_id: Optional[str] = None) -> Dict[str, str]:
    """
    Calls JarvisVision Foundry Agent
    Returns dict: {answer, thread_id}
    """
    try:
        client = get_agent_client()
        if not AGENT_ID:
            raise ValueError("AGENT_ID not set in .env - copy from Foundry")

        # Reuse thread if session already has one, else create new
        if thread_id:
            try:
                thread = client.threads.get(thread_id=thread_id)
            except Exception:
                # If thread doesn't exist or is invalid, create a new one
                thread = client.threads.create()
                thread_id = thread.id
        else:
            thread = client.threads.create()
            thread_id = thread.id

        # Add history (last 6 turns to save tokens)
        if history:
            for h in (history or [])[-6:]:
                role = "user" if h.get("role") == "user" else "assistant"
                text = h.get("text", "")
                if text:
                    client.messages.create(
                        thread_id=thread_id,
                        role=role,
                        content=text
                    )

        # Add current user message with customer context
        context_prefix = ""
        if customer:
            cust_id = customer["id"] if "id" in customer.keys() else str(customer)
            cust_email = customer["email"] if "email" in customer.keys() else ""
            context_prefix = f"[Customer: {cust_id} Email: {cust_email}] "

        client.messages.create(
            thread_id=thread_id,
            role="user",
            content=context_prefix + user_text
        )

        # Process the run
        run = client.runs.create_and_process(
            thread_id=thread_id,
            agent_id=AGENT_ID,
        )

        if run.status == "failed":
            error_msg = run.last_error.message if run.last_error else "Unknown agent error"
            return {"answer": f"Agent failed: {error_msg}", "thread_id": thread_id}

        # Get the latest assistant message
        messages = client.messages.list(thread_id=thread_id, order=ListSortOrder.DESCENDING)
        for msg in messages:
            msg_role = str(getattr(msg, "role", "")).lower()
            if "assistant" in msg_role or "agent" in msg_role:
                if hasattr(msg, "text_messages") and msg.text_messages:
                    item = msg.text_messages[-1]
                    if isinstance(item, dict):
                        text_val = item.get("text", {}).get("value", "")
                    elif hasattr(item, "text"):
                        txt = item.text
                        text_val = txt.get("value", "") if isinstance(txt, dict) else getattr(txt, "value", str(txt))
                    else:
                        text_val = str(item)
                    if text_val:
                        return {"answer": text_val, "thread_id": thread_id}
                elif hasattr(msg, "content") and msg.content:
                    for c in msg.content:
                        if isinstance(c, dict):
                            val = c.get("text", {}).get("value")
                            if val:
                                return {"answer": val, "thread_id": thread_id}

        return {"answer": "I couldn't find an answer in the policy library.", "thread_id": thread_id}
    except Exception as e:
        return {
            "answer": f"Agent service unavailable: {str(e)}",
            "thread_id": thread_id
        }