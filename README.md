# Empório AI Assistant

Customer-support assistant for **Empório da Música**, a fictional music store, built for the Artefact AI Engineer technical case.

## Current state

**Work in progress.** The assistant itself is not implemented yet. Nothing in this version answers questions or calls a language model.

What works today:
- FastAPI backend with one public endpoint, `GET /api/health`, returning `{"status": "ok", "service": "emporio-ai-assistant"}`. It needs no `.env`, credentials, database or model provider.
- React + TypeScript (Vite) page with the store identity, a notice that support is still being prepared, and a live check of the API connection (checking / available / failed states, with a retry button).
- Data preparation (EMP-5): a command that validates the challenge catalog CSVs (categories, products, promotions), builds a local SQLite catalog, and extracts the store manual by page and section with source hashes. Customers and orders are not loaded. See [docs/data.md](docs/data.md).
- Evaluation scenarios (EMP-6): 30 development scenarios with pass criteria in `evals/development_scenarios.json` and a validator for their structure and their references to the prepared data. No model has been run against them. See [docs/evaluation.md](docs/evaluation.md).
- Budget control (EMP-7): a persistent SQLite ledger and one guarded path that reserves the worst-case cost before any future paid call, keeps reservations when the outcome is uncertain and settles reported usage. No price is verified yet, so paid calls are refused; no model SDK is installed. An offline runner drives the scenarios through a placeholder executor and labels every run SIMULATED. See [docs/evaluation.md](docs/evaluation.md).
- Read-only catalog tools (EMP-8): `search_products`, `get_product`, `quote_price`, `list_categories` with strict arguments, availability classes, promotion/PIX rules, ROUND_HALF_UP rounding and installment classification against both manual rules. Not yet used by an agent. See [docs/tools.md](docs/tools.md).
- Offline checks: Ruff and pytest for the backend (synthetic fixtures, no challenge data needed); lint, typecheck and build for the frontend.

Planned next (not available yet): the single-agent chat with Claude Haiku as the first model (EMP-9) and the chat UI (EMP-10). See `docs/plan.md`.

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

## Prepare the challenge data

The challenge sources are not in this repository. Point the command at your local copy of the extracted challenge folder (quotes matter: the folder name has spaces and accents):

```powershell
$src = 'C:\path\to\Desafio Técnico AI Engineer 2026'
.\.venv\Scripts\python.exe -m emporio_assistant.prepare_data `
    --csv-dir "$src\data" `
    --manual "$src\data\políticas_da_loja.pdf" `
    --output-dir data\derived
```

It publishes a new generation (`catalog.sqlite`, `policies.json`, `quality_report.json`) under `data\derived\generations\`, makes it active through `data\derived\current.json` (all git-ignored) and prints counts and warnings. Exit code 1 means validation or publication failed and the active generation was not changed. Code that needs the files resolves them with `emporio_assistant.generations.active_generation`. Schema, money representation, data snapshot and the ambiguities found in the sources are in [docs/data.md](docs/data.md). The API does not use these files yet.

Tests use small synthetic fixtures (`backend/tests/fixtures/synthetic_catalog/`), not the store's data.

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

- No chat, model provider, retrieval or catalog API. The tools run locally only; no endpoint serves them.
- The budget ledger only controls application code that goes through it; it cannot see or stop other spending on provider accounts or Claude Code's own billing.
- No customer or order data: those challenge tables are deliberately not loaded in v1.
- The catalog is a static snapshot of the challenge files, not live inventory. Source ambiguities are documented in `docs/data.md`, not resolved.
- No authentication, deployment or production server configuration; the Vite proxy is for local development only.
- No automated browser tests in the repository; the EMP-4 UI checks were run locally with headless Edge (see `docs/progress.md`).

## AI assistance

This project is planned and developed with AI assistance. ChatGPT helped plan the case, the work breakdown and the development harness. Claude Code (Anthropic) helps implement and verify the code under the owner's direction. The owner reviews every change and makes all commits.
