# Package verification — 2026-11-01

Executed in the artifact build environment, not on Matheus's Windows machine:
- `python scripts/verify.py --mode harness`: PASS, seven unittest methods covering normal read-only commands, Git mutations, protected edits, legitimate project edits, obvious risky commands, invalid/outside-root events and process exit protocol.
- App mode without scaffold: correctly reports APP NOT READY and returns failure; no app completion claim.
- JSON settings/manifest parse checked; required file references present.
- ZIP construction excludes bytecode, credentials, source challenge data and application code; paths are relative and extracted at project root.

Not yet verified:
- Claude Code2.1.314 actually loading these settings/hooks on this Windows installation.
- Local PATH/shell integration, inherited user configuration and authentication/billing mode.
- Any application, browser flow, model adapter, budget ledger or live API result.

Run harness verification locally, then inspect `/permissions`, `/hooks` and `/memory` before implementation. A passing local unit suite does not certify OS isolation or eliminate prompt injection. Source instruction and guard changes need owner review.

## Re-validating hook integration
- The hook runs `python` (exec form, no shell quoting). Python 3.11 must be on the PATH of Claude's processes; if it is missing the hook fails closed and blocks every guarded tool. Do not disable the hook: fix the executable with owner review and re-validate.
- Unit tests check guard logic, not the Claude→hook wiring. To check the wiring, run one innocuous command (e.g. `git status`), then one explicitly blockable but harmless attempt in a supervised session, and record which tools were actually confirmed.
