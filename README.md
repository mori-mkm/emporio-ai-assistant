# Empório AI Assistant

Customer-support assistant for **Empório da Música**, a fictional music store, built for the Artefact AI Engineer technical case.

## Current state

**Work in progress.** The chat works end to end in an offline **simulated** mode. The real model path (Claude Haiku 5.5) is implemented and tested only with a fake client: **no real Haiku call has been made yet**, so answer quality with the real model is not validated.

What works today:
- Chat UI (EMP-10, React + TypeScript + Vite): customer and assistant messages, Enter to send / Shift+Enter for a new line, processing state, "Nova conversa", understandable errors with retry that keeps the typed text, manual sources under each answer, mobile layout. Simulated replies are labelled on screen.
- Chat API (EMP-9, FastAPI): `POST /api/sessions`, `POST /api/messages`, `DELETE /api/sessions/current`, plus `GET /api/health`. The backend owns the session (opaque HttpOnly cookie), history, instructions, model and limits; the browser sends only text and a request id.
- Single agent (EMP-9): one bounded loop - trusted instructions + full store manual -> model -> allowlisted read-only tools -> final answer. Prices, stock, promotions and payment come from tools; the agent never computes money. See [docs/architecture.md](docs/architecture.md).
- Anthropic adapter (EMP-9): official SDK, Messages API, `claude-haiku-5-5`, every call through the budget gate with verified prices. Not exercised against the real API yet.
- Data preparation (EMP-5): validated catalog CSVs -> local SQLite + the store manual extracted by page and section. Customers and orders are not loaded. See [docs/data.md](docs/data.md).
- Evaluation scenarios (EMP-6): 30 development scenarios with pass criteria and a validator. No real model has been run against them. See [docs/evaluation.md](docs/evaluation.md).
- Budget control (EMP-7): persistent SQLite ledger; one guarded path reserves the worst-case cost before each paid call, keeps the reservation when the outcome is uncertain and settles only complete provider usage.
- Read-only catalog tools (EMP-8): `search_products`, `get_product`, `quote_price`, `list_categories`. See [docs/tools.md](docs/tools.md).

Not done: the first real Haiku pilot (prepared, not run), other model adapters, retrieval variants, streaming, deployment.

## Prerequisites

- Windows with PowerShell
- Python 3.11 (`py -3.11`)
- Node.js 24 and npm (tested with Node 24.18.0, npm 11.16.0)

No Docker or global packages are needed. An Anthropic API key is needed only for live mode.

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

## Prepare the challenge data

The challenge sources are not in this repository. Point the command at your local copy of the extracted challenge folder (quotes matter: the folder name has spaces and accents):

```powershell
$src = 'C:\path\to\Desafio Técnico AI Engineer 2026'
.\.venv\Scripts\python.exe -m emporio_assistant.prepare_data `
    --csv-dir "$src\data" `
    --manual "$src\data\políticas_da_loja.pdf" `
    --output-dir data\derived
```

It publishes a new generation (`catalog.sqlite`, `policies.json`, `quality_report.json`) under `data\derived\generations\`, makes it active through `data\derived\current.json` (all git-ignored) and prints counts and warnings. Exit code 1 means validation or publication failed and the active generation was not changed. Code that needs the files resolves them with `emporio_assistant.generations.active_generation`. Schema, money representation, data snapshot and the ambiguities found in the sources are in [docs/data.md](docs/data.md). The chat reads the active generation; a turn never mixes two generations.

Tests use small synthetic fixtures (`backend/tests/fixtures/synthetic_catalog/`), not the store's data.

## Choose a mode

The backend reads **process environment variables** (no `.env` loader; `.env.example` lists them). Set them in the PowerShell window that starts the API:

| `EMPORIO_ASSISTANT_MODE` | Behaviour |
|---|---|
| unset (default) | Chat disabled: `POST /api/sessions` answers 503 `assistant_not_configured`. No mock, no paid call. Health still works. |
| `simulated` | Offline: same API, sessions, agent loop and real tools; only the model is replaced by a scripted reply that says it is simulated. Not evidence of model quality. |
| `live` | Claude Haiku 5.5 (paid). Needs the API key in the backend process, an existing live budget ledger and verified prices; otherwise the chat stays disabled and the reason is logged locally. |

Set the key for live mode without echoing it (Windows PowerShell 5.1; the value never reaches the browser, logs or run reports):

```powershell
$secure = Read-Host 'Anthropic API key' -AsSecureString
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
Remove-Variable secure
$env:EMPORIO_ASSISTANT_MODE = 'live'
```

It lasts only for that PowerShell window; remove it with `Remove-Item Env:ANTHROPIC_API_KEY`. Live mode also needs the live ledger (`budget init --mode live`, owner action, see `docs/development.md`).

## Run (two terminals)

Terminal 1 - API (simulated mode shown):

```powershell
Set-Location backend
$env:EMPORIO_ASSISTANT_MODE = 'simulated'
..\.venv\Scripts\python.exe -m uvicorn emporio_assistant.main:app --host 127.0.0.1 --port 8000
```

Run **one worker**: sessions live in the API process memory and are lost on restart.

Terminal 2 - web UI:

```powershell
Set-Location frontend
npm run dev
```

Open http://127.0.0.1:5173 and type a question. In simulated mode a banner and a "Resposta simulada" badge mark every reply.

| URL | What |
|---|---|
| http://127.0.0.1:5173 | Web UI (Vite dev server) |
| http://127.0.0.1:8000/api/health | Health endpoint |
| http://127.0.0.1:8000/docs | FastAPI interactive docs |

In development, Vite forwards `/api/*` to `http://127.0.0.1:8000` (see `frontend/vite.config.ts`), so the browser only talks to one origin and the API does not enable CORS. Generation endpoints also require an allowed `Origin` (`EMPORIO_ALLOWED_ORIGINS`).

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

- The real Haiku path has only been tested offline with a fake client. Quality, latency and cost with the real model are unmeasured until the pilot runs.
- The prompt asks the model to use tools, cite sources and not invent; that is model behaviour to evaluate, not a guarantee. Code-enforced controls are listed in `docs/architecture.md`.
- Sessions are in memory (one worker, 30-minute expiry, 12 exchanges per conversation).
- The budget ledger only controls application code that goes through it; it cannot see or stop other spending on provider accounts or Claude Code's own billing.
- No customer or order data, no purchases, reservations or handoff to a human.
- The catalog is a static snapshot of the challenge files, not live inventory.
- No authentication, deployment or production server configuration; the Vite proxy is for local development only.
- Browser checks run locally with headless Edge (`.local/`, not in the repository); see `docs/progress.md`.

## AI assistance

This project is planned and developed with AI assistance. ChatGPT helped plan the case, the work breakdown and the development harness. Claude Code (Anthropic) helps implement and verify the code under the owner's direction. The owner reviews every change and makes all commits.
