# Empório AI Assistant

Customer-support assistant for **Empório da Música**, a fictional music store, built for the Artefact AI Engineer technical case.

## Current state

**Work in progress.** The assistant itself is not implemented yet. Nothing in this version answers questions, calls a language model, or reads the store data.

What works today (EMP-4, project scaffold):
- FastAPI backend with one public endpoint, `GET /api/health`, returning `{"status": "ok", "service": "emporio-ai-assistant"}`. It needs no `.env`, credentials, database or model provider.
- React + TypeScript (Vite) page with the store identity, a notice that support is still being prepared, and a live check of the API connection (checking / available / failed states, with a retry button).
- Offline checks: Ruff and pytest for the backend; lint, typecheck and build for the frontend.

Planned next (not available yet): catalog data loading, read-only product/policy tools, the single-agent chat with Claude Haiku as the first model, evaluation and budget controls. See `docs/plan.md`.

## Prerequisites

- Windows with PowerShell
- Python 3.11 (`py -3.11`)
- Node.js 24 and npm (tested with Node 24.18.0, npm 11.16.0)

No Docker, API keys or global packages are needed.

## Setup (once)

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c backend\constraints.txt -e ".\backend[dev]"
Set-Location frontend
npm ci
Set-Location ..
```

`backend/pyproject.toml` declares the direct dependencies; `backend/constraints.txt` pins the full tested set. `frontend/package-lock.json` pins the frontend.

## Run (two terminals)

Terminal 1 — API:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn emporio_assistant.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 — web UI:

```powershell
Set-Location frontend
npm run dev
```

Open http://127.0.0.1:5173. The page should report that the server is available. Stop the API (Ctrl+C in terminal 1) and click **Tentar novamente** to see the failure state.

| URL | What |
|---|---|
| http://127.0.0.1:5173 | Web UI (Vite dev server) |
| http://127.0.0.1:8000/api/health | Health endpoint |
| http://127.0.0.1:8000/docs | FastAPI interactive docs |

In development, Vite forwards `/api/*` to `http://127.0.0.1:8000` (see `frontend/vite.config.ts`), so the browser only talks to one origin and the API does not enable CORS.

## Checks

All offline, no network or model calls:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe scripts\verify.py --mode app
```

This runs the development-harness tests, `ruff check` and `pytest` (with the venv interpreter), then `npm run lint`, `npm run typecheck` and `npm run build`. Individually:

```powershell
Set-Location backend; ..\.venv\Scripts\python.exe -m ruff check .; ..\.venv\Scripts\python.exe -m pytest; Set-Location ..
Set-Location frontend; npm run lint; npm run typecheck; npm run build; Set-Location ..
```

## Limits of this stage

- No chat, model provider, retrieval, catalog data, SQLite, orders or customer data.
- No authentication, deployment or production server configuration; the Vite proxy is for local development only.
- No automated browser tests yet; the browser check for EMP-4 was a manual headless render (see `docs/progress.md`).

## AI assistance

This project is planned and developed with AI assistance. ChatGPT helped plan the case, the work breakdown and the development harness. Claude Code (Anthropic) helps implement and verify the code under the owner's direction. The owner reviews every change and makes all commits.
