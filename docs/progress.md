# Session handoff

## Current state (2026-11-01, Haiku pilot prepared; blocked on owner prerequisites)
- Git: `main` at `28700c3` (EMP-9 + EMP-10 committed). Pilot-prep changes below are uncommitted; Matheus stages/commits.
- Linear: no connector in the Claude session; proposed updates in the session report (EMP-9/10 evidence, EMP-13 pilot prep). No card to Done.
- Implemented: chat UI, session API and single Haiku agent; simulated mode. The real Haiku path is tested ONLY with a fake client.
- Live calls / spend: none. Real Haiku quality NOT validated.
- Pilot authorized in two batches (DEV-001 ≤ US$0.05; six other smoke scenarios ≤ US$0.20) but not run: no live ledger at `data/budget/ledger.sqlite`; API access/billing and Claude Code billing mode not confirmed. Commands in `docs/evaluation.md` "First live pilot".
- Owner pending: confirm API access/billing and Claude Code billing; `budget init --mode live` + `budget summary`; re-check prices; set the key in the PowerShell process; run batch 1, have it reviewed, then batch 2; the 10 reserved validation cases before final validation.

## 2026-11-01 — pilot preparation (run records), pilot not executed
- Run records: `AssistantService.send` takes an optional `diagnostics` dict (runner only; the API never passes it) filled with turn status, model calls, tool names, dropped citations, ledger op ids and data generation; `ModelReply`/`TurnResult` carry the op ids. The runner keeps per-turn records (`turns[]` with status, text, duration) instead of flattening to text; a batch-stopping error is recorded with its public code (`BatchStopped`), with `not_run` listing skipped scenarios. The report adds the repository revision + dirty flag, generator/agent settings, data generations used, run duration and the ledger view of the run. `--max-run-seconds` is now checked before every turn (was: between scenarios).
- Ledger: read-only `Ledger.run_report(run_id)` + CLI `budget run <run_id>`; accounting unchanged.
- Tests (+3 runner, 1 API assertion): answered vs limit_reached vs incomplete kept per turn with op ids matching the ledger and settled cost; provider error stops the batch, keeps the reservation (`uncertain`) and lists `not_run`; no turn starts after the deadline; the public reply has only `request_id/status/mode/reply{text,sources}`.
- Pre-pilot checks (offline): scenarios structure + references OK (92 checks) against active generation `20261101T125243264432Z-10abfa`; `anthropic` 2.3.0 installed; the only provider call site is `AnthropicGenerator.create` through `guarded_call`; `evals/results/` and `data/budget/` git-ignored; first-call prompt bound 24 328 tokens (reservation 12 631 µUSD), far below the 90 000 limit, so no refusal or truncation expected for the smoke scenarios; simulated dry run through the CLI (`--executor simulated --ids DEV-001,DEV-004`) wrote the new report shape; `--executor live` refused before building a client (missing confirmation flag; then missing ledger). Risk to watch in batch 1: if the API omits any cache usage field, settlement is refused and the ledger holds (conservative by design).

## 2026-11-01 — EMP-7 fix, EMP-9 Haiku agent + session API, EMP-10 chat UI
- EMP-7 fix (reviewer repro on `021adc7`: `usage={}` settled at zero and released the reservation): settlement now requires every category in the price entry's `required_usage`; empty/incomplete usage -> `uncertain`, reservation kept, hold, turn/batch stops; explicit zero accepted; prompt tier (100k) enforced at reservation and settlement; per-run batch cap; owner `budget settle` CLI. Round-up to the micro-dollar kept and documented (may overstate < 1 µUSD per category per call).
- Prices: `config/pricing.json` `2026-11-01.1` - Haiku 5.5 only, ≤100k-token tier, sources and date in the file (official model overview + pricing pages checked 2026-11-01).
- Files (backend): `anthropic_adapter.py`, `agent.py`, `assistant.py` (new); `main.py`, `runner.py`, `budget.py`, `pricing.py`, `pyproject.toml` (`anthropic==2.3.0`), `constraints.txt` (+7 pins: anthropic, docstring_parser, httpcore2, httpx2, jiter, sniffio, truststore); tests `test_agent.py`, `test_api.py` (new), `conftest.py`, `test_budget.py`, `test_runner.py`. Frontend: `src/App.tsx`, `src/api.ts`, `src/index.css`. Root: `.env.example`. Docs: README, architecture, development, evaluation, security, progress.
- Design and limits: `docs/architecture.md` "Implemented flow (EMP-9/10)" and the code-vs-model controls table.
- Offline tests (fake Anthropic client, temporary live ledgers): direct policy answer with sources; tool_use -> real tool -> tool_result -> answer; two tools in one call, sequential with correct ids; invalid arguments and unknown tool returned as errors; duplicate call; model-call, tool-call, time and max_tokens limits; refusal; empty answer; timeout, rate limit, 500 -> sanitized 504/503/502, reservation kept, not re-sent on repeat; empty/incomplete/valid usage; SDK built with retries 0; simulated ledger, fictitious prices and above-tier prompt refused; malicious tool text stays in tool_result; Soprano broad search -> agent offers only the soprano item (offline script, model behaviour still to evaluate); two isolated sessions, forged cookie, expiry, new conversation drops the old one; repeated request id -> no new call; concurrent message -> 409; client-supplied system/model/history/role/limits -> 422 without echo; data generation change between turns -> 409 `data_changed`; missing data -> 503 without path; health without assistant config.
- Browser (real input, headless Edge via DevTools Protocol, simulated backend; `.local/chat-check.mjs`): 10/10 steps pass - load with banner/label/regions and focus; Shift+Enter newline without sending, Enter sends, labelled simulated reply with sources, box cleared and focused; continuity and double-Enter sends once; network failure -> alert, text kept, no phantom message; keyboard retry recovers; a reply held in flight is dropped after "Nova conversa"; new session works; Tab/Shift+Tab order with visible focus; 360 px width without horizontal scroll (screenshot reviewed). Bug found and fixed by this check: "Nova conversa" kept the unsent text. Not covered: screen reader output, light theme, real provider latency.
- Checks: see the final block below.
- Limits: real model behaviour (tool use, citations, Soprano selection, tone, injection resistance) unmeasured; no streaming; single worker in-memory sessions; no per-client rate limit.

## 2026-11-01 — EMP-8 read-only tools and deterministic commercial rules
- Files: `backend/emporio_assistant/commerce.py`, `tools.py`, `tests/test_commerce.py`, `tests/test_tools.py`, `docs/tools.md`; scenario contract v2 (`evals/development_scenarios.json`, `scenarios.py`, `test_scenarios.py`, `docs/evaluation.md`).
- Tools: `search_products`, `get_product`, `quote_price`, `list_categories`; strict Pydantic arguments (no coercion, no extra fields), literal accent-insensitive search via `instr`, offer-price filters shared with the quote, allowlisted sorting, limit ≤ 10. One `CatalogSnapshot` per operation pins one generation; catalog opened `mode=ro` + `query_only`. Sanitized error codes; diagnostics only in the local log.
- Rules: availability classes; one active promotion → offer price, several → `needs_confirmation`; PIX 5% on list price only; ROUND_HALF_UP once to the cent (prototype convention); installments classified against both manual rules (`supported` / `not_supported` / `needs_confirmation`), split keeps the exact total; split-payment PIX and R$ 500,00 / contacts left unresolved.
- Contract change (before any benchmark run): scenario money now has one expected value, ROUND_HALF_UP (`discounted_price.expected_cents`; DEV-010 stacked value R$ 5.243,13, DEV-019 R$ 151,91, DEV-022 R$ 569,91). The validator recomputes it with `Decimal`, independently of `commerce.py`.
- Real-data cross-check (offline, generation `20261101T125243264432Z-10abfa`): tool quotes equal all 8 product-based scenario `discounted_price` values; installment amounts/classes match DEV-011 (4x supported, 5x needs confirmation, 6x not supported) and DEV-020 (12x R$ 183,25 supported); available sets match DEV-001/005/021; availability of 96/136/130 = out_of_stock/discontinued/coming_soon; empty categories 6–8 reported. One expected difference: a "Soprano" text search also returns tenor 124 because its description mentions soprano (search covers descriptions; documented).
- Not done: no agent or prompt-injection test with a model; no API endpoint for the tools.

## 2026-11-01 — EMP-7 budget ledger, guarded call path and offline runner
- Files: `backend/emporio_assistant/pricing.py`, `budget.py`, `runner.py`, `config/pricing.json`, `tests/test_budget.py`, `tests/test_runner.py`; `.gitignore` adds `data/budget/`.
- Ledger: SQLite in micro-USD, `BEGIN IMMEDIATE` per write, explicit `init`, missing file = error; states reserved/dispatched/settled/released/uncertain; holds on overrun or unpriceable usage; caps 7/2 USD, margin 1 USD never used by code.
- Tests (fictitious prices): insufficient budget refused before dispatch; reservation lowers availability; known usage settled; repeated settle idempotent, conflicting settle refused; timeout/connection error/missing usage keep the reservation; `NotSent` releases it; reopen preserves state, missing ledger not recreated; 8 processes racing for 2 USD each → exactly 3 reserved; SDK retries refused, reused op id refused, retry with a new id counted; unknown price/missing or zero output limit refused; shipped config has no priceable model; margin and the other budget untouched; overrun recorded and blocks until `clear-hold`; live ledger refuses fictitious prices. Runner: SIMULATED/ungraded label, no expected answers in the output, per-scenario history, errors recorded, budget errors stop the run.
- CLI checks: `runner --smoke` wrote a SIMULATED run to `evals/results/` (ignored); `budget init/summary` on a `.local` ledger; a missing ledger → "FAILED: budget ledger is missing or unreadable", no file created.
- Limits: the ledger only governs code calling `guarded_call`; adapters must pass `timeout_s`, disable SDK retries and raise `NotSent` only when nothing was sent; a reservation whose `dispatched` write failed stays held until reconciled; real usage mapping per provider is verified with each adapter.

## 2026-11-01 — checks for EMP-7 + EMP-8
- `ruff format --check`, `ruff check` clean; `pytest` 125 passed (1 known `StarletteDeprecationWarning`); scenarios structure OK and references OK (92 checks); `verify.py --mode app` with `.venv` Python and `PYTHONDONTWRITEBYTECODE=1` → HARNESS PASS + APP OFFLINE CHECKS PASS.

## 2026-11-01 — EMP-6 development scenarios and evaluation contract
- Files: `evals/development_scenarios.json` (contract v1, DEV-001–030, smoke suite of 7), `backend/emporio_assistant/scenarios.py` (validator CLI), `backend/tests/test_scenarios.py`; `docs/evaluation.md` gained the contract, distribution, smoke rationale, human rubric, critical failures and the reserved-case protocol.
- Content: user messages and pass criteria only, no assistant replies or results; facts referenced by id/section/page (no CSV or manual copy); 10 multi-turn, 4 in English; money values recomputed with integer cents, both cents accepted where PIX leaves half a cent (no rounding rule in the sources).
- Checks: structure OK without data (clean-clone mode, references reported NOT CHECKED); with `--data-dir data\derived` → references OK, 92 checks against active generation `20261101T125243264432Z-10abfa` (source hashes equal `data_version`). Names, descriptions and specs quoted in expectation text were checked by hand against the same generation. `pytest` 45 passed; Ruff format/check clean; `verify.py --mode app` → APP OFFLINE CHECKS PASS.
- Not done: no model run, scorer or LLM judge; no accuracy/cost/latency figures. DEV-028/029 need test doubles that the future runner must provide. Reserved cases not created, read or searched for.

## 2026-11-01 — EMP-5 fix: publish prepared data as one consistent generation
- Problem (owner review of `d2c88b0`): `prepare` replaced `catalog.sqlite`, `policies.json` and `quality_report.json` with three separate renames; a failure after the first left a new catalog next to old policies/report.
- Fix: new `backend/emporio_assistant/generations.py`. Each run writes `generations/<id>/` (three files, fsynced), `prepare_data` reads them back (JSON parse, SQLite integrity, row counts), then `promote` replaces `current.json` with one `os.replace`. `active_generation(dir)` is the only resolver: one manifest read, fixed file names inside `generations/<id>/`, SHA-256 check, rejects malformed/incomplete generations. Old generations and the legacy root files are never deleted automatically (the CLI prints a note about legacy files). CLI returns 1 with a clear message for validation errors and for expected operational errors (`GenerationError`, `OSError`, `sqlite3.Error`); other exceptions propagate.
- Tests (`backend/tests/test_generations.py`, observed through `active_generation`): success resolves all three files from one generation; injected failure writing `policies.json` and `quality_report.json` (after the new catalog exists) keeps the previous active generation byte-for-byte; injected failure of the manifest `os.replace` keeps it and leaves no manifest temp; failed first run leaves no active generation; rerun publishes equal content and keeps the old generation; invalid sources and an operational error return 1 with a message; resolver rejects path escape, wrong file name, missing entry, hash mismatch, unknown format, broken JSON and a deleted file. CLI tests moved here from `test_policies.py`.
- Checks: `ruff format --check`, `ruff check` clean; `pytest` 36 passed. Real run → active generation `20261101T125243264432Z-10abfa`; its `source_files` hashes and the manual hash match the current source files; catalog rows equal the legacy root file; `policies.json` equals the legacy one except line endings (legacy was written in text mode with CRLF, now LF). `git check-ignore` covers `data/derived/current.json` and `data/derived/generations/**`.
- Limits: no power-loss guarantee at the rename instant; inactive generation folders accumulate until removed manually.

## 2026-11-01 — EMP-5 validated catalog, SQLite and sourced policies
- EMP-4 leftovers: interactive check with real input events (`.local/cdp-retry-check.mjs`, local and ignored: headless Edge driven via DevTools Protocol with Node's built-in WebSocket; it starts and stops its own Vite/uvicorn/Edge). Results: API down on load → failure text; Tab focuses "Tentar novamente" with `:focus-visible` and a solid 3px outline; Enter → available; mouse click with API down → failure; Space → available. No leftover processes. Not covered: screen readers, light theme. `main.py` comment corrected (CORS limits browser reads, not requests; not auth).
- Code: `backend/emporio_assistant/catalog.py` (CSV validation, SQLite build with temp file + checks + `os.replace`), `policies.py` (pypdf layout extraction, sections from the PDF outline), `prepare_data.py` (CLI + `emporio-prepare-data` entry point). Health endpoint untouched; it never opens the DB.
- Dependency: `pypdf==6.21.4` (pure Python, BSD-3) added to `pyproject.toml`; `constraints.txt` gained only that line (verified equal to `pip freeze --exclude-editable`). No other version changes.
- Extraction finding: pypdf default mode yields one word per line and flattened tables on this PDF; layout mode was verified page by page and is used.
- Real run (`--csv-dir "Desafio Técnico AI Engineer 2026\data" --manual "...\políticas_da_loja.pdf" --output-dir data\derived`): exit 0; categories 9, products 65, promotions 25 (4 active), manual 8 pages / 29 sections; 0 errors, 9 warnings (product 96 active with 0 stock; 136 discontinued with 13; 130 coming_soon with 2; categories 6/7/8 unused; products 93/96/99 with two inactive promotion rows). Second run through the entry point: identical catalog content and identical `policies.json`.
- Checks: `ruff format --check` and `ruff check` clean; `pytest` 29 passed (synthetic fixtures + generated PDF, temp dirs); `verify.py --mode app` with `.venv` Python and `PYTHONDONTWRITEBYTECODE=1` → HARNESS PASS + APP OFFLINE CHECKS PASS; `git check-ignore -v` confirms `data/derived/*` and `.local/*` ignored.
- Docs: `docs/data.md` (command, schema, money, snapshot, source evidence/ambiguities), README, development, plan (reconciled with owner decisions), architecture status line.
- Harness: the guard blocked a Write to the session scratchpad (outside the project root); the browser script was placed in the ignored `.local/` instead. No protected file changed.
- Open: owner decisions on contacts divergence, installment rule precedence, discontinued-with-stock and coming_soon handling (EMP-8); distribution of the original data and of the extracted manual; `StarletteDeprecationWarning` (httpx) remains.

## 2026-11-01 — EMP-4 monorepo scaffold (Python/FastAPI + React/TypeScript)
- Intent: reproducible local backend + frontend, health check visible in the UI, offline checks wired into `verify.py --mode app`.
- Backend (`backend/`): package `emporio_assistant`, `main.py` with `GET /api/health` (no provider/db/file/credential access). No CORS middleware: Vite proxies `/api` → `http://127.0.0.1:8000`, so the browser uses one origin. Deps pinned in `pyproject.toml` (fastapi 0.162.0, uvicorn 0.54.0; dev: pytest 9.2.0, ruff 0.17.3, httpx 0.28.1 for TestClient); full set in `constraints.txt` from a clean `.venv` (`pip freeze --exclude-editable`, no absolute paths). Tests: health contract; foreign origin gets no CORS grant (GET + preflight).
- Frontend (`frontend/`): official `create-vite@9.3.3 --template react-ts` (Oxlint, TypeScript 7.0.2, Vite 8.3.5, React 19.3.1; 28 packages), demo assets/README removed, `typecheck` script added, status page with checking/available/failed states + retry, `role="status"`, visible focus, pt-BR text stating support is not active yet. Native `fetch` with 5 s timeout in `src/api.ts` (single API location).
- Root: `README.md` (English), `.gitignore` + `*.egg-info/`, `docs/development.md` commands. No `.env.example` (no configuration needed yet).
- Checks run (all exit 0): `.venv` Python `ruff check` (+ `ruff format --check`), `pytest` 2 passed; `npm run lint|typecheck|build`; `npm ci` from lockfile; `PYTHONDONTWRITEBYTECODE=1 .venv\Scripts\python.exe -B scripts/verify.py --mode app` → HARNESS PASS + APP OFFLINE CHECKS PASS.
- Smoke (real processes, then stopped): `curl` 127.0.0.1:8000/api/health and via Vite 127.0.0.1:5173/api/health → 200 + expected JSON; foreign `Origin` → no `Access-Control-Allow-Origin`. With API stopped the proxy returns 502.
- Browser: headless Edge (temporary profile) screenshots of the real page: "Servidor disponível…" with API up; "Não foi possível conectar…" with API down. Not tested: clicking retry, keyboard focus, light theme, screen reader; no automated browser tests yet.
- `git check-ignore -v` confirmed the accented source folder, ZIPs, `.venv`, egg-info, `node_modules` and `dist` are ignored (closes the earlier open item).
- Limitations: pytest shows `StarletteDeprecationWarning` (Starlette 1.10 recommends `httpx2` for TestClient). Fetching `httpx2` was blocked by the auto-mode classifier and its provenance wasn't verified, so httpx stays. On Windows, stopping the background `npm run dev` left an orphan `node` (Vite) process; it was stopped by its PID.

## 2026-11-01 — retired START_HERE.md
- Git now initialized by Matheus: `main` tracking `origin/main`, first commit `269dd3a`; tree was clean before this change.
- Removed the install guide `START_HERE.md` (harness already installed and validated) and only its entry in `HARNESS_MANIFEST.json` `files`; other hashes/metadata untouched.
- Still-relevant guidance moved: hook PATH requirement and integration-probe procedure → `docs/verification.md`; "stage exact paths, never `git add .`" → `docs/development.md`. Extraction steps dropped as obsolete; auth/billing, `/permissions`/`/hooks`/`/memory` and secrets guidance already lived in AGENTS.md/docs.
- Manifest hashes for `docs/verification.md`, `docs/development.md` and `docs/progress.md` now intentionally differ (hashes are provenance; verifier checks existence).
- Check: `PYTHONDONTWRITEBYTECODE=1 python -B scripts/verify.py --mode harness` → 7/7 OK, HARNESS PASS, exit 0; manifest parses with 20 entries.

## History — 2026-11-01 (harness relocation check, pre-EMP-4)
- Root: `C:\Users\mathe\Documents\projects\case\emporio-ai-assistant`. At that time not yet a Git repository (since initialized by Matheus; see current state).
- Harness files sit at the root (no wrapper folder). All 21 files listed in `HARNESS_MANIFEST.json` match their SHA-256 (checked before this progress edit; this file's hash now intentionally differs).
- Original sources preserved and untouched: `Desafio_Técnico_AI_Engineer_2026-20260616T150750Z-3-001.zip`, `emporio-ai-assistant-harness-v1.zip`, folder `Desafio Técnico AI Engineer 2026/` (6 CSVs + `data/políticas_da_loja.pdf` + `desafio_tecnico_ai_eng_artefact.pdf`). Extracted file sizes equal the ZIP entries.
- Tooling observed: Python 3.11.9 (`...\Python311\python.exe` on PATH), Git 2.54.0.windows.1, Claude Code 2.1.316 (manifest targeted 2.1.314).

### Checks actually run
1. Harness logic (offline): `PYTHONDONTWRITEBYTECODE=1 python -B scripts/verify.py --mode harness` → 7/7 unittest OK, `HARNESS PASS`, exit 0. No new bytecode: the two existing `__pycache__/*.pyc` files keep their 03:55:31 timestamp (created before this session).
2. Claude→hook integration on Windows (live, harmless probes in this session):
   - PowerShell and Bash: an echo containing `git add` was denied by `guard.py` ("Git mutation/staging is manual").
   - Write: writing `hook-probe.zip` was denied ("Preserve original challenge sources"); no file created.
   - Edit: NOT independently confirmed (the probe failed tool validation before the hook ran). NotebookEdit not tested. Same matcher entry as Write, so likely wired, but unverified.
   - Read deny: `Read(.env)` refused by permission settings.
3. Application: `verify.py --mode app` → `APP NOT READY`, exit 1 (expected; no app exists).

### Permissions / inherited configuration
- Project `settings.json` declares `defaultMode: "default"`, but this session ran in **auto mode** (user settings contain an `autoMode` block). Project deny rules and the PreToolUse hook still applied. Owner decides which mode to use.
- No `~/.claude/settings.local.json`; `~/.claude/CLAUDE.md` is empty; no user-level hooks/permissions/env in `~/.claude/settings.json`.
- User-level plugins inject context: `ponytail` (minimal-code persona), `claude-mem` (stores session observations under the user profile, outside the repo, and asks to use its work-state tool). claude.ai connectors (Gmail, Drive, Calendar, Docs) are available to the session with account-wide scope; none used. `plugin:data:definite` MCP failed to connect. No conflict blocked work, but claude-mem persistence means dataset descriptions may leave the workspace.

### Dataset counts (csv.DictReader, UTF-8, no data printed)
| file | DictReader records | columns |
|---|---|---|
| categories | 9 | 3 |
| customers | 50 | 5 |
| order_items | 22 | 3 |
| orders | 20 | 9 |
| products | 65 | 9 |
| promotions | 25 | 5 |
Total 191 records. No BOM, no blank lines, no multi-line quoted fields, no ragged rows. Files have NO trailing newline: `wc -l` returns physical lines − 1 (9, 50, …), and the previous audit then subtracted the header again, producing the off-by-one counts (8, 49, 21, 19, 64, 24).

### Changed files this session
- `.gitignore` (new): ZIPs, the original challenge folder, real `.env*`/credentials (keeps `.env.example`), Claude local files, venvs/caches/bytecode, `node_modules`, SQLite DBs, logs, `evals/results/`, `.private/`, `evals/holdout/`. Does not ignore CSV/PDF generically. Excluding the sources is temporary and does not settle the data distribution policy.
- `docs/progress.md` (this file).

### Live calls / spend
None this session. Prior project API consumption: unknown.

## Open before EMP-4
- Owner confirms Claude Code auth (subscription vs API billing) via `/status` before any paid test.
- Owner chooses permission mode (auto vs project `default`).
- ~~After `git init`: confirm ignore patterns~~ — done during EMP-4 (`git check-ignore -v`).
- Optional: confirm the Edit-tool hook with a supervised probe.
- Stale `__pycache__` folders (ignored) can be removed manually.
- `evals/results/` and `logs/` are provisional names; adjust when EMP-7 defines output paths. Decide data distribution (customers.csv contains personal fields) before EMP-5.

## Next action
Matheus reviews the pilot-prep changes, resolves the prerequisites, runs batch 1 (DEV-001) and shares the run id for review before batch 2. Luna/Qwen adapters, BM25 and embeddings not started.

## Update template
- Date / EMP issue:
- Intent and acceptance criteria:
- Changed files:
- Checks actually run / results:
- Live calls and project cumulative spend (or unknown):
- Remaining limitations / failures:
- Owner changes to preserve:
- Next concrete step:
