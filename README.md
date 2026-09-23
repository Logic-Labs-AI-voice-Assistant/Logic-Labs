# Logic Labs Internal Voice IT Helpdesk

Logic Labs is an internal, voice-enabled IT helpdesk for a company whose computers and operating procedures are custom-built. It is intended to run inside the company's controlled environment for employees and authorized support staff, not as a public customer service product.

The helpdesk keeps company information inside company-approved systems and gives answers grounded in the company's own computer designs, policies, diagnostics, and approved solutions. A customer can sign in, describe an IT problem by typing or speaking, attach an image or PDF, receive a grounded response, and create and manage support tickets.

> **Internal deployment requirement:** This repository contains application code, not a complete security boundary. A production installation must use the company's private identity, network, Azure tenant, storage, logging, and access controls. Do not send real company data to a personal Azure subscription, an unapproved public AI service, or a public demo deployment.

This document is written for two audiences:

- **Non-technical users:** the first sections explain what the application does in everyday language.
- **Developers or evaluators:** the later sections provide the exact steps to clone, configure, run, test, and understand the project.

## What the Project Does

Imagine a digital IT support desk that is available from a browser on the company's private network. The employee signs in and can:

1. Ask a support question in a chat window.
2. Use the microphone to speak the question instead of typing it.
3. Attach a screenshot, photograph, or PDF describing the problem.
4. Have the application read text from the attachment and ask the AI assistant to explain it using company-approved knowledge.
5. Listen to an answer using computer-generated speech.
6. Create a support ticket when the problem needs follow-up.
7. View or remove their own tickets from the dashboard.

The application keeps each employee's account and tickets separate. Someone signed in as one employee cannot view another employee's tickets through the normal API flow. Production access should additionally be restricted to company identity groups, private network routes, and least-privilege roles.

## How It Works in Plain Language

The project has three main parts:

- **The web pages:** The `frontend/` folder contains the screens that the user sees: a landing page, login/register page, and chat/helpdesk page.
- **The helpdesk service:** The FastAPI application in `backend/` receives requests from the browser, checks the signed-in user, manages conversations, stores tickets, and serves the web pages.
- **Company-controlled cloud helpers:** Azure AI Foundry, Azure Speech, and Microsoft Entra ID can provide the AI agent, speech recognition, speech synthesis, and company sign-in, but they must be deployed in the company's approved tenant and region with the required privacy controls. The application can start without the AI agent configured, but it will not provide grounded production answers.

The normal journey is:

`Browser -> FastAPI backend -> authentication/database -> Azure service when enabled -> browser`

SQLite is used for local customer and ticket data in the development version. Conversation sessions and login sessions are kept in memory, so restarting the backend ends active sessions and clears active conversations. Production should use company-managed durable storage and a controlled session store.

## Company Grounding Model

This helpdesk is designed for custom-built company computers, so a generic answer is not sufficient. A useful answer should be based on the company's actual environment, for example:

- approved hardware models, components, firmware, drivers, and operating-system images;
- internal network, security, software-installation, and repair procedures;
- known error messages and the approved fix for each error;
- hardware-specific troubleshooting steps and escalation rules;
- previous resolved tickets, when company policy permits their reuse.

The AI should answer from these approved sources, identify the relevant computer model or error, provide the supported solution, and say when the issue must be escalated. It should not invent a workaround, recommend unapproved software, or expose another employee's ticket or diagnostic information.

For every production deployment, maintain a company-owned knowledge source with an owner, revision date, access policy, and removal process. Attachments and ticket text should be treated as confidential company data. Retrieval, prompts, logs, transcripts, and model outputs must follow the company's retention and access policies.

### Important implementation boundary

The current code sends chat and document-analysis prompts to the Azure AI Foundry agent configured in `backend/.env`; it does not yet enforce a private network, implement a retrieval index, or verify that every answer came from an approved company source. Those controls must be added to or wrapped around the deployment before real company data is used. The README describes the required operating model; it does not claim that the present development configuration provides those controls automatically.

## Main Features

### Accounts and authentication

- Local registration and login with email and password.
- Password validation requires at least eight characters, uppercase and lowercase letters, a number, and a special character.
- Passwords are stored as PBKDF2-SHA256 hashes rather than plain text.
- Optional Microsoft Entra ID sign-in can be enabled with Azure credentials.
- Login sessions use an HTTP-only cookie.

### AI helpdesk chat

- A signed-in customer starts a conversation session.
- Messages are sent to the configured Azure AI Foundry agent.
- Recent conversation history is reused to keep replies relevant.
- The customer identity is included as context for the agent.
- If the agent is unavailable or not configured, the backend returns a useful status message instead of crashing.

### Voice support

- Browser speech recognition can turn spoken words into a chat message.
- Azure Speech can provide speech tokens, transcribe uploaded audio, and synthesize the assistant's answer.
- Multiple English and international language options are listed for automatic language detection.

### Image and PDF analysis

- Authenticated users can upload common image formats or PDF documents.
- PDF text is extracted with `pypdf` when available.
- Text in images is extracted with EasyOCR.
- The extracted content and the user's question are sent to the AI agent for an explanation or policy check.

### Tickets

- Customers can create tickets with an issue description.
- Tickets are stored in SQLite and associated with the creating customer.
- Customers can list and delete their own tickets.

## Repository Layout

```text
Logic-Labs/
├── backend/
│   ├── main.py             FastAPI application and ticket/chat endpoints
│   ├── auth.py             Local and Microsoft authentication helpers
│   ├── agent_service.py    Azure AI Foundry agent integration
│   ├── speech_service.py   Azure Speech endpoints
│   ├── vision_service.py   Image/PDF extraction and analysis endpoint
│   ├── .env                Local secrets; create this file yourself
│   └── customer_auth.db    Local SQLite database; created/used at runtime
├── frontend/
│   ├── index.html          Landing page
│   ├── Login.html          Login and registration screen
│   └── main.html           Chat, voice, upload, and ticket interface
├── tests/
│   └── test_auth.py        Authentication, tickets, API, and upload tests
├── requirements.txt        Python dependencies
└── README.md               This guide
```

## Reproduce the Project from GitHub

### Prerequisites

Install or obtain the following before starting:

- Git
- Python 3.10 or newer
- A modern browser such as Chrome, Edge, or Firefox
- Internet access for package installation and cloud services

For the complete internal experience, also prepare the company-approved Azure resources described in [Azure configuration](#azure-configuration). The local account, ticket, HTML, and API functionality can be tested with synthetic data without connecting an AI agent. Do not use real company data during local testing unless the machine, account, network, and cloud resources are approved for that data classification.

### 1. Clone the repository

```bash
git clone https://github.com/Logic-Labs-AI-voice-Assistant/Logic-Labs.git
cd Logic-Labs
```

### 2. Create and activate a virtual environment

Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dependency list includes FastAPI/Uvicorn, authentication libraries, Azure SDKs, EasyOCR, and image-processing packages. Installation may take some time because OCR and machine-learning packages are included. `pytest` is an optional development dependency and is installed separately below when you want to run the tests.

### 4. Create the local environment file

The repository intentionally does not include secrets. Create a file named `backend/.env` and add values appropriate for your own Azure resources:

```dotenv
# Company Entra ID only. Leave blank for synthetic local testing.
TENANT_ID=your-tenant-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
REDIRECT_URI=http://localhost:8000/api/auth/microsoft/callback

# Company Azure AI Foundry project containing the grounded support agent.
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project
JARVIS_AGENT_NAME=JarvisVision
JARVIS_AGENT_VERSION=9

# Required for Azure Speech features.
AZURE_SPEECH_KEY=your-speech-key
AZURE_SPEECH_REGION=your-speech-region

# Local runtime settings.
SECRET_KEY=replace-with-a-long-random-value
DATABASE_PATH=backend/customer_auth.db
SESSION_TTL_SECONDS=3600
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
```

For local development, `CLIENT_SECRET` and `AZURE_SPEECH_KEY` are only needed for the features that use them. Never commit `backend/.env`, API keys, client secrets, company documents, diagnostic images, ticket exports, or a production database. The `.gitignore` file is already configured to exclude environment files and SQLite databases. Use synthetic data unless the local environment is company-approved.

### 5. Start the backend

Run this command from the repository root, with the virtual environment active:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Open these addresses in a browser:

- Landing page: `http://127.0.0.1:8000/`
- Login/register: `http://127.0.0.1:8000/login`
- Helpdesk page: `http://127.0.0.1:8000/main`
- Health information: `http://127.0.0.1:8000/api/health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

Using the backend to serve the frontend is recommended because the pages use relative `/api/...` URLs and the backend manages the authentication cookie. Do not open `frontend/main.html` directly from the file system for the normal flow.

### 6. Use the application

1. Open `http://127.0.0.1:8000/` and choose the login/register option.
2. Register a local account using a valid email and a strong password such as `Password123!`.
3. Sign in and open the helpdesk.
4. Type a question or use the microphone button.
5. Attach a PNG, JPG, or PDF if the issue includes a screenshot or document.
6. Create a ticket from the dashboard when the issue needs tracking.

Without `FOUNDRY_PROJECT_ENDPOINT`, `JARVIS_AGENT_NAME`, and `JARVIS_AGENT_VERSION`, the application still starts and the UI can be explored with synthetic data, but chat replies will say that the agent is not configured. Without an Azure Speech key, speech token/transcription/synthesis calls will report a configuration error. Do not treat the fallback response as a production support solution.

## Azure Configuration

Production Azure resources must belong to the company's approved tenant and subscription. Configure private endpoints, firewall rules, managed identities, encryption, diagnostic-log access, retention, and regional data residency according to company policy. Do not use a personal subscription or an unapproved public endpoint for company data.

1. **Azure AI Foundry:** use a company-managed project and `JarvisVision` agent whose grounding sources contain approved custom-computer documentation, known errors, supported fixes, and escalation rules. Set `FOUNDRY_PROJECT_ENDPOINT`, `JARVIS_AGENT_NAME`, and `JARVIS_AGENT_VERSION`.
2. **Grounding source:** connect the agent to the company's approved knowledge repository or retrieval index. Keep source ownership, versioning, permissions, and citations available for audit. This repository is not implemented by the current starter code and must be supplied by the company deployment.
3. **Azure Speech:** use a company-approved Speech resource and set `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION`. Treat audio, transcripts, and synthesized responses as company data.
4. **Microsoft Entra ID:** register the application in the company tenant, restrict sign-in to approved users or groups, create a client secret or managed identity, and add `http://localhost:8000/api/auth/microsoft/callback` as a development redirect URI. Use the company production callback for deployment.
5. Ensure the identity used by the application has only the permissions required to use the AI Foundry project, grounding data, Speech resource, and storage.

The backend uses `DefaultAzureCredential` for the Foundry client. In production, prefer a company-managed workload identity or managed identity over long-lived API keys. Depending on the environment, authenticate with Azure CLI or another supported credential mechanism during development. The application reads the project endpoint and agent ID from environment variables.

## API Overview

All chat, ticket, speech, and vision routes except public health/static routes require an authenticated customer. In a company deployment, the service should also be reachable only through the private company network or approved zero-trust gateway; authentication alone is not a reason to expose it to the public internet.

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/auth/register` | Create a local customer account |
| `POST` | `/api/auth/login` | Sign in and set the session cookie |
| `GET` | `/api/auth/me` | Return the current customer |
| `POST` | `/api/auth/logout` | End the current session |
| `GET` | `/api/auth/microsoft/login` | Start Microsoft sign-in |
| `POST` | `/api/session/start` | Start a protected conversation |
| `POST` | `/api/message` | Send a message to the helpdesk agent |
| `GET` | `/api/tickets` | List the signed-in customer's tickets |
| `POST` | `/api/tickets` | Create a ticket |
| `DELETE` | `/api/tickets/{ticket_id}` | Delete one of the customer's tickets |
| `GET` | `/api/speech/languages` | List supported speech languages |
| `GET` | `/api/speech/token` | Obtain a short-lived browser speech token |
| `POST` | `/api/speech/transcribe` | Convert an audio upload to text |
| `POST` | `/api/speech/synthesize` | Convert text to audio |
| `POST` | `/api/vision/analyze` | Extract and analyze an image or PDF |
| `GET` | `/api/health` | Show service-loading and configuration status |

The `/docs` page provides request schemas and a convenient way to try the endpoints.

## Run the Tests

From the repository root with the virtual environment active:

```bash
python -m pip install pytest
python -m pytest -q
```

The tests use a temporary SQLite database for authentication and tickets. They cover registration, login/logout, invalid credentials, ticket creation/listing/deletion, chat session creation, HTML routes, health status, and a PDF upload path.

## Troubleshooting

### `ModuleNotFoundError` or missing packages

Confirm that `.venv` is active, then run:

```bash
python -m pip install -r requirements.txt
```

### The browser shows a login redirect

Use the URL served by Uvicorn, such as `http://127.0.0.1:8000/`, rather than opening an HTML file directly. Also confirm that cookies are enabled.

### The AI answer says the agent is not configured

Check `backend/.env` for `FOUNDRY_PROJECT_ENDPOINT`, `JARVIS_AGENT_NAME`, and `JARVIS_AGENT_VERSION`, restart Uvicorn after changing the file, and check `/api/health`.

### Microsoft login fails

Verify the tenant ID, client ID, client secret, and redirect URI. The redirect URI in Azure must exactly match `http://localhost:8000/api/auth/microsoft/callback` when using the default local settings.

### Speech or upload analysis fails

Check the relevant Azure key and region. Image analysis also initializes EasyOCR on first use, which can be slow. Only image files and PDF documents are accepted by `/api/vision/analyze`.

### Port 8000 is already in use

Start the server on another port:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8001
```

When changing the port, update `REDIRECT_URI` and `ALLOWED_ORIGINS` as needed.

## Current Scope and Limitations

- Local login and ticket storage use SQLite and are intended for development or a small demonstration, not as the final store for confidential company records.
- Login sessions and conversation sessions are stored in process memory, so they do not survive a server restart and are not shared between multiple backend workers.
- Ticket `category` and `priority` are accepted by the frontend form but the current database/API response stores the issue and status only.
- The current starter code does not provide private networking, retrieval-augmented grounding, source citations, data-loss prevention, tenant isolation at the infrastructure layer, or a formal audit trail for AI answers.
- Production deployment should use a company-managed database, durable session storage, HTTPS, secure cookies, secret management, private endpoints, least-privilege identity, approved grounding sources, redacted logs, retention controls, and stronger operational monitoring.
- Before launch, the company should test that prompts, attachments, audio, transcripts, tickets, logs, backups, and AI-provider telemetry stay within the approved company boundary.

## License

See [LICENSE](LICENSE) for the project's license information.