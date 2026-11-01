# Session handoff

## Current state (2026-11-01, after EMP-4 work — awaiting Matheus review)
- Git: `main` tracking `origin/main` (commits `269dd3a`, `f057769`); EMP-4 changes uncommitted, Matheus stages/commits.
- App scaffold exists: FastAPI `GET /api/health` + Vite React-TS status page. No chat, model, data, SQLite or budget code yet.
- Linear: no Linear connector in the Claude session; EMP-4 criteria taken from the owner prompt + `docs/plan.md`. Linear updates for EMP-3/EMP-4 proposed to Matheus, not applied.
- Live calls / spend: none. Claude Code billing mode still unconfirmed (required before any paid test).

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
Matheus reviews EMP-4 (manual two-terminal run in README), commits it, and updates Linear. Suggested next card: EMP-5 (validated data, SQLite catalog, sourced policies), after deciding the data distribution policy.

## Update template
- Date / EMP issue:
- Intent and acceptance criteria:
- Changed files:
- Checks actually run / results:
- Live calls and project cumulative spend (or unknown):
- Remaining limitations / failures:
- Owner changes to preserve:
- Next concrete step:
