# Session handoff

## 2026-11-01 — retired START_HERE.md
- Git now initialized by Matheus: `main` tracking `origin/main`, first commit `269dd3a`; tree was clean before this change.
- Removed the install guide `START_HERE.md` (harness already installed and validated) and only its entry in `HARNESS_MANIFEST.json` `files`; other hashes/metadata untouched.
- Still-relevant guidance moved: hook PATH requirement and integration-probe procedure → `docs/verification.md`; "stage exact paths, never `git add .`" → `docs/development.md`. Extraction steps dropped as obsolete; auth/billing, `/permissions`/`/hooks`/`/memory` and secrets guidance already lived in AGENTS.md/docs.
- Manifest hashes for `docs/verification.md`, `docs/development.md` and `docs/progress.md` now intentionally differ (hashes are provenance; verifier checks existence).
- Check: `PYTHONDONTWRITEBYTECODE=1 python -B scripts/verify.py --mode harness` → 7/7 OK, HARNESS PASS, exit 0; manifest parses with 20 entries.

## Verified state — 2026-11-01 (harness relocation check, pre-EMP-4)
- Root: `C:\Users\mathe\Documents\projects\case\emporio-ai-assistant`. Not a Git repository (no `.git`); no remote. Git init/remote/first commit are Matheus's manual steps.
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
- After `git init`: run `git status --ignored` and `git check-ignore -v` on the ZIPs/source folder to confirm the accented folder pattern matches.
- Optional: confirm the Edit-tool hook with a supervised probe.
- Stale `__pycache__` folders (ignored) can be removed manually.
- `evals/results/` and `logs/` are provisional names; adjust when EMP-7 defines output paths. Decide data distribution (customers.csv contains personal fields) before EMP-5.

## Next action
Matheus initializes Git and makes the first commit manually. Then EMP-4: minimal FastAPI health endpoint + Vite React/TS shell, wired into `verify.py --mode app` (pyproject, pytest, Ruff, npm lint/typecheck/build).

## Update template
- Date / EMP issue:
- Intent and acceptance criteria:
- Changed files:
- Checks actually run / results:
- Live calls and project cumulative spend (or unknown):
- Remaining limitations / failures:
- Owner changes to preserve:
- Next concrete step:
