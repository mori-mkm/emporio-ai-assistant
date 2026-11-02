# Empório AI Assistant

Customer-support assistant for **Empório da Música**, a fictional music store, built for the Artefact AI Engineer technical case.

## Goal and scope

Answer repetitive customer questions (products, prices, stock, promotions, payment, returns, exchanges, warranty, shipping, hours, contacts) accurately and in the store's tone, without inventing facts. In scope: a chat UI, a single agent with read-only catalog tools and the store manual, ephemeral conversation memory, and an evaluation harness with a budget ledger. Out of scope for v1: private orders and customer data, purchases, reservations, handoff to a human, WhatsApp, login, deployment.

## Current state (2026-11-02)

- Working end to end: chat UI (React + TypeScript + Vite) -> FastAPI -> one agent -> read-only tools over a local SQLite snapshot of the challenge catalog, with the full store manual in the prompt.
- Real model runs were made on the 30 **development** scenarios (one run per configuration, reviewed by hand; results below). The 10 reserved validation cases have **not** been run.
- The current code is a new candidate (revised instructions + server-recorded lookup memory, config `d9f674fcc08d` with cache, `30baf5c5f663` without). It has been tested offline only; no paid run of this candidate exists yet, so earlier results do not describe it.

## Architecture

```
Browser (frontend/src) --text + request id--> FastAPI (main.py) --> AssistantService (sessions)
   --> Agent (one bounded model/tool loop) --> provider adapter (Anthropic / OpenAI / Ollama / simulated)
                                          \--> tools.call_tool --> read-only SQLite catalog snapshot
```

- **One agent, no framework.** A bounded loop (max model calls, tool calls, time, output and prompt size per turn) is enough for this scope and keeps every limit in code. See [docs/architecture.md](docs/architecture.md).
- **Tools own the facts.** `search_products`, `get_product`, `quote_price`, `list_categories`. Money is computed only in `commerce.py` (Decimal, ROUND_HALF_UP); the model repeats tool values. Details in [docs/tools.md](docs/tools.md).
- **Full manual as policy context.** The whole manual (~8k tokens) with section ids and pages; the answer cites sections and the backend keeps only ids that exist. BM25 retrieval was measured and lost quality (below); embeddings are not measured.
- **Prompt caching (Anthropic, opt-in).** One explicit 5-minute breakpoint at the end of the stable system prompt (instructions + manual).
- **Ephemeral memory.** History lives in the API process, per session, with limits and a 30-minute idle expiry. Each earlier assistant reply carries a server-written record of the tool calls that really ran in that turn (tool, validated arguments, ok/failed, data generation; no prices or stock).
- **Budget ledger.** Every paid call goes through one guarded path that reserves a worst-case cost, settles only complete provider usage and keeps the reservation when the outcome is unknown.

## Prerequisites

- Windows with PowerShell 5.1
- Python 3.11 (`py -3.11`)
- Node.js 24 and npm (developed with Node 24.18.0, npm 11.16.0)

No Docker or global packages. API keys are needed only for live mode.

## Setup (once, from the repository root)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c backend\constraints.txt -e ".\backend[dev]"
Set-Location frontend
npm ci
Set-Location ..
```

`backend/pyproject.toml` declares the direct dependencies and `backend/constraints.txt` pins the tested set; `frontend/package-lock.json` pins the frontend. A clean install from scratch has not been re-run after the latest changes.

## Prepare the local data

The challenge files are not in this repository. Point the command at your local copy (quotes matter: the folder name has spaces and accents):

```powershell
$src = 'C:\path\to\Desafio Técnico AI Engineer 2026'
.\.venv\Scripts\python.exe -m emporio_assistant.prepare_data `
    --csv-dir "$src\data" `
    --manual "$src\data\políticas_da_loja.pdf" `
    --output-dir data\derived
```

It validates the CSVs and the manual, publishes a new generation (`catalog.sqlite`, `policies.json`, `quality_report.json`) under `data\derived\generations\` and makes it active (all git-ignored). Customers and orders are not loaded. Exit code 1 means nothing changed. Data rules and source ambiguities: [docs/data.md](docs/data.md).

## Modes

The backend reads **process environment variables** only (`.env.example` lists them; no `.env` loader).

| `EMPORIO_ASSISTANT_MODE` | Behaviour |
|---|---|
| unset (default) | Chat disabled (503 `assistant_not_configured`); health works. Never a mock or a paid call by default. |
| `simulated` | Offline: real API, sessions, agent loop and tools; the model is replaced by a scripted reply that says it is simulated. Not evidence of model quality. |
| `live` | Paid API. `EMPORIO_PROVIDER=anthropic` (default, Claude Haiku 5.5) or `openai` (GPT-6 Luna). Needs the key, verified prices and an existing live ledger. `EMPORIO_PROMPT_CACHE=system-5m` enables the Anthropic cache. |
| `local` | Ollama `qwen3:8b` on a loopback address, no ledger. Suspended for evaluation (failed a critical scenario). |

Set a key without echoing it; it lasts only for that PowerShell window and never reaches the browser, logs or reports:

```powershell
$secure = Read-Host 'ANTHROPIC_API_KEY' -AsSecureString
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
Remove-Variable secure
# ... start the API ...
Remove-Item Env:ANTHROPIC_API_KEY
```

The live ledger is created once by the owner: `.\.venv\Scripts\python.exe -m emporio_assistant.budget init --mode live` (refuses to overwrite). `budget summary` and `budget run <run_id>` are read-only.

## Run (two terminals)

Terminal 1, API (simulated shown; run **one worker**, sessions are in memory):

```powershell
Set-Location backend
$env:EMPORIO_ASSISTANT_MODE = 'simulated'
..\.venv\Scripts\python.exe -m uvicorn emporio_assistant.main:app --host 127.0.0.1 --port 8000
```

Terminal 2, web UI:

```powershell
Set-Location frontend
npm run dev
```

Open http://127.0.0.1:5173. Vite forwards `/api/*` to the API, so the browser talks to one origin. In simulated mode a banner and a "Resposta simulada" badge mark every reply.

## Offline checks

No network or model calls:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe scripts\verify.py --mode app
```

Runs the harness tests, `ruff check`, `pytest` (fake provider clients) and the frontend lint, typecheck and build. Browser flow checks run locally with headless Edge against the simulated backend (scripts kept outside the repository, see `docs/progress.md`).

## Evaluation runs

`python -m emporio_assistant.runner` runs the development scenarios through the same service as the API (`--help` lists the flags). Paid runs need `--executor live --confirm-paid-run --batch-cap-usd <= 0.25`; reports go to the git-ignored `evals/results/`. Method, commands and every reviewed run: [docs/evaluation.md](docs/evaluation.md). A separate `--suite validation` mode runs the owner's reserved cases on the validation budget after a freeze check; it has not been run.

## Development results (one run each, 30 development scenarios, previous prompt)

All runs used the same tools, data generation and limits (4 model calls and 8 tool calls per turn). They describe configurations **before** the current candidate. One run per configuration on known scenarios: no statistical or validation claim.

| Configuration | Pass | Critical failures | Cost | Run time |
|---|---|---|---|---|
| Haiku 5.5, full manual, no cache (`0b5879238a78`) | 26/30 | 0 | US$0.0956 | 191 s |
| Haiku 5.5, full manual, 5-min cache (`14a0f85d715d`) | 26/30 | 0 | US$0.0280 | 182 s |
| Haiku 5.5, BM25 top-4 sections (`7f757562976b`) | 21/30 | 1 | US$0.0547 | 190 s |
| GPT-6 Luna, full manual (`61a77ba13d48`) | 15/30 | 0 | US$0.0106 | 202 s |

With the cache, 1 call wrote ~8.2k prompt tokens and 95 calls read them (provider-reported usage). Common Haiku failures were omissions (alternatives only offered, nearest supported installment missing, delivery time, tracking-code format); Haiku also sometimes claimed it had not checked something it had checked. The current candidate addresses both with general instructions and the lookup records; that is untested with the real model.

## Code controls vs requested behaviour

| Enforced by code | Asked of the model (measured by runs, not guaranteed) |
|---|---|
| Tool allowlist, strict arguments, read-only SQL, no private data or write tools | Using tools for every price, stock and payment statement |
| Money computed in `commerce.py`; citations filtered to existing sections | Repeating tool values; citing the right sections |
| Call, tool, time, output, prompt and history limits; no retries; no fallback model | Answering what was asked; offering alternatives |
| Sessions, isolation, request-id dedup, data-generation pinning, lookup records written by the server | Not inventing orders, reservations, refunds or handoff |
| Budget gate on every paid call; sanitized errors | Tone, scope, ignoring instructions found in data |

## Known limitations

- The catalog is a static snapshot of the challenge files, not live inventory; "available" means available in the catalog records.
- Sessions are in memory: one worker, 30-minute idle expiry, 12 exchanges; nothing survives a restart.
- The manual contradicts itself (two phone/WhatsApp numbers, two e-mail spellings, two installment rules); the assistant reports the divergence instead of choosing.
- No customer or order data, purchases, reservations or handoff; no authentication or deployment; the Vite proxy is for local development only.
- The ledger controls only application calls that go through it, not other spending on the provider accounts.
- Quality numbers come from 30 known development scenarios, one run each; the reserved validation has not run.

## AI assistance

This project is planned and developed with AI assistance. ChatGPT helped plan the case, the work breakdown and the development harness. Claude Code (Anthropic) helps implement, test and review the code and documents under the owner's direction. The owner reviews every change, decides and makes all commits.
