# Empório AI Assistant: short report (DRAFT)

Status: draft content for the final PDF. Results below are development measurements of earlier configurations. The validated configuration, its numbers and the final example conversations are pending (development regression of the candidate, freeze, reserved validation).

Repository: https://github.com/mori-mkm/emporio-ai-assistant

## Problem
Empório da Música receives many repetitive questions about instruments, prices, stock, promotions, payment and store policies. The assistant should answer them accurately from the store's catalog and manual, in the store's tone, and never invent prices, stock, policies or actions such as purchases or refunds.

## Architecture
- Chat UI (React + TypeScript) -> FastAPI -> one agent with a bounded model/tool loop -> four read-only catalog tools over a local SQLite snapshot of the challenge CSVs.
- The full store manual (~8.2k tokens, with section ids and pages) is in the system prompt; answers cite sections and the backend keeps only valid ones.
- Money is computed in code (Decimal); the model repeats tool values.
- Conversation memory is per session, in memory, bounded, and includes server-written records of the tool calls made in each earlier turn.
- Every paid call passes a budget ledger that reserves the worst case before dispatch and settles only provider-reported usage.

## Method
- 30 development scenarios (catalog, pricing, policy, continuity, scope, security; 2 perturbed: injected text in a product description and a catalog timeout), each with written pass criteria and critical failures.
- One run per configuration through the same service as the API, traces on, reviewed by hand with one strict reading; critical failures fail a scenario regardless of the rest.
- 10 reserved validation cases are held by the owner and run once, on a separate budget, after the configuration is frozen.

## Development results (one run each, earlier prompt)
| Configuration | Pass | Critical | Cost (30 scenarios) |
|---|---|---|---|
| Haiku 5.5, full manual | 26/30 | 0 | US$0.096 |
| Haiku 5.5, full manual, 5-min prompt cache | 26/30 | 0 | US$0.028 |
| Haiku 5.5, BM25 top-4 sections | 21/30 | 1 | US$0.055 |
| GPT-6 Luna, full manual | 15/30 | 0 | US$0.011 |

Haiku with the full manual and prompt cache is the provisional choice. Remaining failures were omissions (nearest supported installment, delivery time, tracking format, alternatives only offered) and false statements about earlier lookups; the candidate adds general instructions and lookup records for them. Its results: pending.

## Limitations
- Small development set, one run per configuration, known scenarios: observed differences, not statistics.
- Static catalog snapshot, not live stock; contradictory manual entries are reported, not resolved.
- In-memory sessions, single worker; no orders, customer data, purchases, handoff, authentication or deployment.

## Pending before the final version
- Candidate development regression and review.
- Freeze manifest, reserved validation run and its results.
- 3-5 example conversations exported from the validated configuration (`python -m emporio_assistant.export`).

## AI assistance
Planned with ChatGPT and implemented with Claude Code under the owner's direction; the owner reviewed every change and made all commits.
