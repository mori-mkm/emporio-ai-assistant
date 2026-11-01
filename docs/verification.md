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
