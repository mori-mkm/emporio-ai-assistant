# Development workflow and completion gates

## Sources of truth
Assignment PDFs: requirements and store policies (with documented ambiguities).
Linear EMP project: priority/status/acceptance. Repo docs: technical decisions and reproducible instructions. Code/tests: verified behavior. A narrative is not proof.

## Small slices
Begin with an offline health endpoint and frontend connection, load/validate data, deterministic search/pricing, then the smallest Haiku tool loop. Integrate every slice before adding optional features. Do not run all benchmark permutations before a smoke test.
Separate responsibilities without building a framework: API contracts, agent loop, provider adapters, tools/business rules, repository/query layer, retrieval and evaluation runner. Avoid dependency cycles and frontend secrets.

## Verification layers
1. Deterministic: money, stock/status, SQL parameters, tool schemas, session boundaries and budget reservations. Offline tests for risks/regressions.
2. Integration: API and frontend contracts with labeled provider stubs. Confirm errors, timeout handling and cancelled/failed responses. No paid APIs in default pytest/CI.
3. Browser: a few Playwright user-visible flows (send a message, follow-up, new isolated session, provider error). Don't test internal CSS/component names as business outcomes.
4. Live model eval: explicit bounded runs using the production path and recorded usage; see evaluation plan. Mocks never stand in for these results.

## Commands
Layout (EMP-4): `backend/` (FastAPI package `emporio_assistant`, pytest, Ruff, pins in `pyproject.toml` + full set in `constraints.txt`) and `frontend/` (Vite React-TS, npm scripts dev/lint/typecheck/build, `package-lock.json`). One Python venv at the repo root: `.venv`.
- Setup: `py -3.11 -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -c backend\constraints.txt -e ".\backend[dev]"`; `npm ci` in `frontend/`.
- Python dependency update: change pins in `backend/pyproject.toml`, recreate `.venv`, install without `-c`, run checks, regenerate `constraints.txt` with `pip freeze --exclude-editable` (keep its header).
- Offline checks: `.\.venv\Scripts\python.exe scripts/verify.py --mode app` (runs the harness tests, Ruff and pytest with the same venv interpreter, then npm lint/typecheck/build). Run it with the venv Python, not the global one, or backend checks fail on missing packages. Set `$env:PYTHONDONTWRITEBYTECODE='1'` to avoid bytecode.
- Harness only: `python scripts/verify.py --mode harness`.
The verifier FAILS if the app is not scaffolded. Browser/live smoke checks are separate and explicitly reported. A build alone is not an end-to-end test. Don't create empty scripts to fake success.
- Data (EMP-5): `.\.venv\Scripts\python.exe -m emporio_assistant.prepare_data --csv-dir "<challenge>\data" --manual "<challenge>\data\políticas_da_loja.pdf" --output-dir data\derived` (also `emporio-prepare-data`). It publishes a generation under `data\derived\generations\` activated by `data\derived\current.json`; read artifacts only via `generations.active_generation`. Outputs and rules in `docs/data.md`. `data/derived/` and `.local/` are git-ignored; tests use `backend/tests/fixtures/synthetic_catalog/` and never the challenge files.
- Scenarios (EMP-6): `.\.venv\Scripts\python.exe -m emporio_assistant.scenarios --scenarios evals\development_scenarios.json --data-dir data\derived` (omit `--data-dir` in a clean clone: structure only, references reported NOT CHECKED). Contract and rubric in `docs/evaluation.md`.
- Budget (EMP-7): `.\.venv\Scripts\python.exe -m emporio_assistant.budget init --mode live` once (creates `data\budget\ledger.sqlite`, refuses to overwrite), then `... budget summary` / `... budget clear-hold <op_id> --note "..."`. Offline runner: `.\.venv\Scripts\python.exe -m emporio_assistant.runner --smoke` (or `--ids DEV-001,DEV-004`) writes a SIMULATED run to `evals\results\`.
- Tools (EMP-8): `.\.venv\Scripts\python.exe -m emporio_assistant.tools schemas`; pipe JSON arguments, e.g. `'{"product_id": 95}' | .\.venv\Scripts\python.exe -m emporio_assistant.tools get_product -`. See `docs/tools.md`.
- Dev servers: API on `127.0.0.1:8000`, UI on `127.0.0.1:5173`; Vite proxies `/api` to the API (target in `frontend/vite.config.ts`), so the backend has no CORS middleware.
- Chat (EMP-9/10): set `EMPORIO_ASSISTANT_MODE` (`simulated` offline, `live` paid; unset = chat disabled) in the API's PowerShell window, then start uvicorn with ONE worker. Variables are listed in `.env.example`; the backend reads the process environment only. Live mode also needs the API key in that process (see README for a no-echo PowerShell snippet), `config/pricing.json` with verified prices and an existing live ledger.
- Interactive browser check (local, `.local/chat-check.mjs`, not versioned): `node .local/chat-check.mjs` from the repo root starts uvicorn (simulated), Vite and headless Edge, drives real keyboard/mouse input over the DevTools Protocol and prints PASS/FAIL per step. It needs ports 8000, 5173 and 9333 free.
- Budget settle (owner): `... budget settle <op_id> --usage '{"input": 1200, "output": 300, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0}'` records usage confirmed in the provider console for an `uncertain` operation; incomplete usage is refused.
- Ledger view of one run (read-only): `... budget run <run_id>` prints that run's operations, reported usage, reserved/confirmed µUSD and open holds.
- Runs: Haiku pilot and second smoke run in `docs/evaluation.md`. Runner executors: `placeholder`, `simulated`, `live --provider anthropic|openai` (paid: `--confirm-paid-run --batch-cap-usd`), `local` (Ollama, loopback only; `--num-ctx`, `--num-predict`). Reports state `execution` (`simulated` / `local_inference` / `paid_api`). Comparison commands: `docs/evaluation.md` "Model comparison runs".
- Chat server provider: `EMPORIO_ASSISTANT_MODE` (`live` / `local` / `simulated`) plus optional `EMPORIO_PROVIDER` (`anthropic` default, `openai`) and `EMPORIO_OLLAMA_URL` (loopback http, default `http://127.0.0.1:11434`).

## Before marking ready for owner review
- Selected acceptance criteria exercised; outputs checked, no invented PASS.
- Relevant offline checks pass; live evidence present only when actually run.
- Diff reviewed for secrets, data leakage, unjustified dependencies, duplicate abstractions and scope creep.
- Tests weren't weakened merely to pass. Legitimate requirement/rubric changes explained for owner review.
- Progress contains commands, outcomes and remaining gaps; Linear comment/status only in EMP if connected and within authorized work.
- Say "ready for Matheus review"; do not represent owner approval or submission.

## Git and dependencies
Matheus stages/commits/pushes. Suggest a message and the exact paths to stage; never `git add .` blindly (original ZIPs and personal challenge data sit in the root). Never reset or discard user changes. No generated mass commit or force-push.
Check license/maintenance before template reuse; adapt minimal patterns, not entire stacks. Use lockfiles and current compatible versions. No installs from random curl|shell commands. Dependencies need a reason, not a popularity claim.
CI should run offline checks without model credentials. A small workflow is enough; third-party actions need immutable references or supported dependency locking. No deploy or paid benchmark on every push.

## End-of-session handoff
Update docs/progress.md: current EMP task, changed files, checks/results, actual API spend or unknown, known failures, uncommitted user changes and next concrete action. Keep summaries short. Preserve the latest verified state; do not paste huge logs or full transcripts into always-loaded instructions.
