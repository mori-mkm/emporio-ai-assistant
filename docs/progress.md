# Session handoff

## Current state (2026-11-01, EMP-5 fix and EMP-6 ready for Matheus review)
- Git: `main` at `d2c88b0` (EMP-5 committed). EMP-5 generation fix and EMP-6 uncommitted; Matheus stages/commits (two commits suggested).
- Linear: no connector in the Claude session; proposed: EMP-5 stays In Review with the fix noted, EMP-6 In Review. EMP-7 (budget control) is next and not started.
- Implemented: health API + status page (EMP-4); offline data preparation published as generations (EMP-5); 30 development scenarios + validator (EMP-6). No agent, tools, catalog API, budget control or model code yet.
- Live calls / spend: none. Claude Code billing mode still unconfirmed (required before any paid test).
- Owner pending: create and hold the 10 reserved validation cases outside the repository (protocol in `docs/evaluation.md`).

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
Matheus reviews and commits the EMP-5 generation fix and EMP-6 (separate commits), and creates the 10 reserved cases outside the repo. Next card: EMP-7 (minimal budget control) before any paid call.

## Update template
- Date / EMP issue:
- Intent and acceptance criteria:
- Changed files:
- Checks actually run / results:
- Live calls and project cumulative spend (or unknown):
- Remaining limitations / failures:
- Owner changes to preserve:
- Next concrete step:
