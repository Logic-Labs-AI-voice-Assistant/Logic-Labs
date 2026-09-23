import os
import re
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT") or os.getenv("PROJECT_ENDPOINT")
AGENT_ID = os.getenv("JARVIS_AGENT_NAME") or os.getenv("AZURE_AGENT_ID") or os.getenv("AGENT_ID", "JarvisVision")
AGENT_VERSION = os.getenv("JARVIS_AGENT_VERSION", "9")

NO_CODE_REPLY = "I can help explain the issue and guide you through the steps, but I cannot provide source code or scripts."
CODE_PATTERNS = (
    re.compile(r"```", re.IGNORECASE),
    re.compile(r"^\s*(?:def|class|function|const|let|var|import|from)\s+", re.MULTILINE),
    re.compile(r"^\s*(?:#!/|<\?php|#include\s+|using\s+namespace\s+)", re.MULTILINE),
)

_client = None

def get_agent_client() -> AIProjectClient:
    global _client
    if _client is None:
        if not PROJECT_ENDPOINT:
            raise ValueError("Missing required environment variable: FOUNDRY_PROJECT_ENDPOINT")

        _client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
    return _client

def _contains_code(text: str) -> bool:
    return any(pattern.search(text) for pattern in CODE_PATTERNS)

def _guard_response(text: str) -> str:
    return NO_CODE_REPLY if _contains_code(text) else text

def call_jarvis_vision(user_text: str, history: List[Dict] = None, customer: Optional[Dict] = None, thread_id: Optional[str] = None) -> Dict[str, str]:
    """
    Calls the JarvisVision Foundry Agent through the Responses API.
    Returns dict: {answer, thread_id}
    """
    try:
        client = get_agent_client()
        input_messages = []
        for item in (history or [])[-6:]:
            text = item.get("text", "")
            if text:
                input_messages.append({
                    "role": "user" if item.get("role") == "user" else "assistant",
                    "content": text,
                })

        context_prefix = ""
        if customer:
            cust_id = customer["id"] if "id" in customer.keys() else str(customer)
            cust_email = customer["email"] if "email" in customer.keys() else ""
            context_prefix = f"[Customer: {cust_id} Email: {cust_email}] "

        input_messages.append({"role": "user", "content": context_prefix + user_text})
        response = client.get_openai_client().responses.create(
            input=input_messages,
            extra_body={
                "agent_reference": {
                    "name": AGENT_ID,
                    "version": AGENT_VERSION,
                    "type": "agent_reference",
                }
            },
            instructions=(
                "You are JarvisVision, an IT helpdesk assistant. "
                "Never output source code, scripts, commands, code blocks, or configuration snippets. "
                "If asked for code, politely refuse and provide a plain-language explanation or safe steps instead."
            ),
        )
        answer = _guard_response(response.output_text or "I couldn't find an answer in the policy library.")
        return {"answer": answer, "thread_id": getattr(response, "id", None)}
    except Exception as e:
        return {
            "answer": f"Agent service unavailable: {str(e)}",
            "thread_id": thread_id
        }