# Empório AI Assistant: short report (DRAFT)

Status: draft content for the final PDF. Results below are development measurements (one run per configuration). The delivered configuration, its validation numbers and the final example conversations are pending (embeddings experiment, freeze, reserved validation).

Repository: https://github.com/mori-mkm/emporio-ai-assistant

## Problem
Empório da Música receives many repetitive questions about instruments, prices, stock, promotions, payment and store policies. The assistant should answer them accurately from the store's catalog and manual, in the store's tone, and never invent prices, stock, policies or actions such as purchases or refunds.

## Architecture
- Chat UI (React + TypeScript) -> FastAPI -> one agent with a bounded model/tool loop -> four read-only catalog tools over a local SQLite snapshot of the challenge CSVs.
- The full store manual (~8.2k tokens, with section ids and pages) is in the system prompt; answers cite sections and the backend keeps only valid ones. Retrieval selectors (BM25, embeddings) plug into the same contract and are chosen by backend configuration only.
- Money is computed in code (Decimal); the model repeats tool values.
- Conversation memory is per session, in memory, bounded, and includes server-written records of the tool calls made in each earlier turn.
- Every paid call passes a budget ledger that reserves the worst case before dispatch and settles only provider-reported usage.

## Method
- 30 development scenarios (catalog, pricing, policy, continuity, scope, security; 2 perturbed: injected text in a product description and a catalog timeout), each with written pass criteria and critical failures.
- One run per configuration through the same service as the API, traces on, reviewed by hand with one strict reading; critical failures fail a scenario regardless of the rest.
- 10 reserved validation cases are held by the owner and run once, on a separate budget, after the configuration is frozen.

## Development results (one run each)
| Configuration | Prompt | Pass | Critical | Cost (30 scenarios) |
|---|---|---|---|---|
| Haiku 5.5, full manual | previous | 26/30 | 0 | US$0.096 |
| Haiku 5.5, full manual, 5-min prompt cache | previous | 26/30 | 0 | US$0.028 |
| Haiku 5.5, BM25 top-4 sections | previous | 21/30 | 1 | US$0.055 |
| GPT-6 Luna, full manual | previous | 15/30 | 0 | US$0.011 |
| Candidate: Haiku 5.5, full manual, cache, lookup records | revision 2 | 27/30 | 0 | US$0.030 |

Haiku with the full manual and prompt cache is the provisional choice. The candidate's general instructions and server-written lookup records were measured together: two earlier omissions fixed, one new omission, two remaining (metro delivery time, tracking-code format); no false statements about earlier lookups in this run. Embeddings retrieval is implemented and will be compared with BM25 and the full manual (same prompt, cache off) before the configuration is frozen.

## Limitations
- Small development set, one run per configuration, known scenarios: observed differences, not statistics.
- Static catalog snapshot, not live stock; contradictory manual entries are reported, not resolved.
- In-memory sessions, single worker; no orders, customer data, purchases, handoff, authentication or deployment.

- Prototype: not a production service.

## Pending before the final version
- Embeddings experiment (retrieval coverage, then paired answer runs) and the retrieval decision.
- One repeat of the finalists, freeze manifest from the committed HEAD, reserved validation run and its results.
- 3-5 example conversations exported from the delivered configuration (`python -m emporio_assistant.export`), kept in Portuguese as executed (not translated), including at least one non-trivial case.

## AI assistance
Planned with ChatGPT and implemented with Claude Code under the owner's direction; the owner reviewed every change and made all commits.
