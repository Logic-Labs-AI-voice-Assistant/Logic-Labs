# Voice IT Helpdesk — Person 5 (Backend / Frontend / DevOps)

## Running the backend

```bash
cd backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The API runs at `http://127.0.0.1:8000`. Interactive docs (auto-generated
by FastAPI) are at `http://127.0.0.1:8000/docs` — useful for testing
endpoints without the frontend.

## Running the frontend

No build step needed yet. Just open `frontend/index.html` directly in
a browser, or serve it so `fetch` calls behave consistently:

```bash
cd frontend
python -m http.server 5500
```

Then visit `http://127.0.0.1:5500`.

## What's here (Week 1)

- `backend/main.py` — FastAPI app with mock endpoints:
  - `POST /api/session/start`
  - `POST /api/message`
  - `GET/POST /api/tickets`, `DELETE /api/tickets/{id}`
  - `GET /api/auth/me`, `POST /api/auth/logout`
- `frontend/` — chat view + tickets dashboard, calling the endpoints above

## Next steps (Week 2+)

- Swap the mock reply in `/api/message` for a real call to Person 2's
  Foundry Agent
- Swap in-memory `TICKETS` dict for Person 3's MCP `create_ticket()` /
  `get_ticket()` tools
- Add real authentication (currently `/api/auth/*` is fully mocked)
- Deploy to Azure App Service, wire up Application Insights
