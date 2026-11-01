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
Existing now: `python scripts/verify.py --mode harness`.
After app scaffolding, adopt the simple defaults the included verifier expects: backend/pyproject.toml, frontend/package.json, pytest, Ruff and npm scripts lint/typecheck/build. Update these conventions deliberately if the actual layout differs; don't create empty scripts to fake success.
`python scripts/verify.py --mode app` runs offline backend/frontend checks only and FAILS if the app is not scaffolded. Browser/live smoke checks are separate and explicitly reported. A build alone is not an end-to-end test.

## Before marking ready for owner review
- Selected acceptance criteria exercised; outputs checked, no invented PASS.
- Relevant offline checks pass; live evidence present only when actually run.
- Diff reviewed for secrets, data leakage, unjustified dependencies, duplicate abstractions and scope creep.
- Tests weren't weakened merely to pass. Legitimate requirement/rubric changes explained for owner review.
- Progress contains commands, outcomes and remaining gaps; Linear comment/status only in EMP if connected and within authorized work.
- Say "ready for Matheus review"; do not represent owner approval or submission.

## Git and dependencies
Matheus stages/commits/pushes. Suggest a message only. Never reset or discard user changes. No generated mass commit or force-push.
Check license/maintenance before template reuse; adapt minimal patterns, not entire stacks. Use lockfiles and current compatible versions. No installs from random curl|shell commands. Dependencies need a reason, not a popularity claim.
CI should run offline checks without model credentials. A small workflow is enough; third-party actions need immutable references or supported dependency locking. No deploy or paid benchmark on every push.

## End-of-session handoff
Update docs/progress.md: current EMP task, changed files, checks/results, actual API spend or unknown, known failures, uncommitted user changes and next concrete action. Keep summaries short. Preserve the latest verified state; do not paste huge logs or full transcripts into always-loaded instructions.
