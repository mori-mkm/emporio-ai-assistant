# EMP project map (snapshot; Linear is current scheduling authority)

Project: https://linear.app/matheus-mori/project/emporio-ai-assistant-artefact-8d69c9f4ef5f
Team: Empório AI, EMP. Repo: https://github.com/mori-mkm/emporio-ai-assistant
Do not refer to old MAT identifiers as active tasks.

## Status (reconciled with the owner's Linear review, 2026-11-01)
- EMP-3 done: harness installed and verified (see progress).
- EMP-4 in review: close only what has evidence.
- EMP-5 current increment: validated catalog data, SQLite catalog, sourced policies.

| Card | Deliverable | Target |
|---|---|---|
| EMP-1 | Owner creates Anthropic key | Nov1 |
| EMP-2 | Owner creates OpenAI project/key | Nov1 |
| EMP-3 | Claude harness and incremental implementation guidance | Done |
| EMP-4 | Python/FastAPI + React/TypeScript setup | In review |
| EMP-5 | Validated data, SQLite catalog, sourced policies | Current |
| EMP-6 | 30 development scenarios; owner reserves 10 | Nov1–2 |
| EMP-7 | Minimal budget control, measurements and initial eval runner | Nov2 |
| EMP-8 | Read-only tools and deterministic security boundaries | Nov2 |
| EMP-9 | Haiku full-manual baseline and session memory | Nov2 |
| EMP-10 | React chat connected end-to-end | Nov2 |
| EMP-11 | Luna and Qwen adapters | Nov3 |
| EMP-12 | BM25 and embedding retrieval variants | Nov3 |
| EMP-13 | Real incremental model/retrieval benchmark | Nov3 |
| EMP-14 | Evidence-based architectural decision | Nov3 |
| EMP-15 | Frozen held-out validation and security review | Nov4 |
| EMP-16 | English README, real examples, presentation package | Nov4 |
| EMP-17 | Owner submits, records final SHA and freezes repo | Nov4 |
| EMP-18 | Optional auth/private orders | Backlog |
| EMP-19 | Optional hosting/Supabase | Backlog |

## Settled decisions
- Milestones: foundation and working support by Nov 2; comparisons by Nov 3; validation and delivery by Nov 4. Adopted deadline: Nov 4 2026, 23:59 America/Sao_Paulo (owner decision, not recruiter confirmation).
- API keys do not require standalone paid tests. Minimal budget control exists before any paid call.
- v1 conversation history: temporary, in the backend, isolated per session, with expiry and size limits.
- The benchmark starts with the Haiku baseline; it does not wait for every adapter and retrieval mechanism.
- Comparison: Haiku, GPT-6 Luna and Qwen3:8b; full manual, BM25 and embeddings with local storage.
- Scenarios: 30 for development (5–8 used first), 10 reserved and controlled by the owner.
- Up to 480 executions is the planned maximum, not a consumption target.
- Budget: US$10 total; development up to US$7, validation up to US$2, US$1 margin not automatically available.
- Out of the first delivery: Supabase, hosting, WhatsApp, login and private orders.

Dates are owner planning targets, not generated reminders or guaranteed recruiter deadlines.
Do not mark any application feature done before it exists and is checked.

Critical sequence: EMP-3/4 → data/security/offline tests/budget → baseline → UI → comparison/decision → validation/docs/submission. Auth, orders, hosted database and deploy are not prerequisites.
