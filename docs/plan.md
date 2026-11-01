# EMP project map (snapshot; Linear is current scheduling authority)

Project: https://linear.app/matheus-mori/project/emporio-ai-assistant-artefact-8d69c9f4ef5f
Team: Empório AI, EMP. Repo: https://github.com/mori-mkm/emporio-ai-assistant
Do not refer to old MAT identifiers as active tasks.

| Card | Deliverable | Initial target |
|---|---|---|
| EMP-1 | Owner creates Anthropic key | Nov1 |
| EMP-2 | Owner creates OpenAI project/key | Nov1 |
| EMP-3 | Claude harness and incremental implementation guidance | Nov1 |
| EMP-4 | Python/FastAPI + React/TypeScript setup | Nov1 |
| EMP-5 | Validated data, SQLite catalog, sourced policies | Nov1 |
| EMP-6 | 30 development scenarios; owner reserves 10 | Nov1 |
| EMP-7 | Budget ledger, measurements and initial eval runner | Nov1 |
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

Dates are owner planning targets, not generated reminders or guaranteed recruiter deadlines. Final adopted deadline: Nov4 2026,23:59 America/Sao_Paulo.
Do not mark EMP-3 done merely because this ZIP exists: owner still needs to extract and validate local hook integration and billing choice. Do not mark any application feature done before it exists and is checked.

Critical sequence: EMP-3/4 → data/security/offline tests/budget → baseline → UI → comparison/decision → validation/docs/submission. Auth, orders, hosted database and deploy are not prerequisites.
