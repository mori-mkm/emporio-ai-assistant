# Empório AI Assistant: short report

Status: final content for the delivery. Development results are one to three runs per configuration on 30 known scenarios; the reserved validation is 10 cases run once on the frozen configuration.

Repository: https://github.com/mori-mkm/emporio-ai-assistant

## Problem
Empório da Música receives many repetitive questions about instruments, prices, stock, promotions, payment and store policies. The assistant should answer them accurately from the store's catalog and manual, in the store's tone, and never invent prices, stock, policies or actions such as purchases or refunds.

## Architecture
- Chat UI (React + TypeScript) -> FastAPI -> one agent with a bounded model/tool loop -> four read-only catalog tools over a local SQLite snapshot of the challenge CSVs.
- The full store manual (~8.2k tokens, with section ids and pages) is in the system prompt; answers cite sections and the backend keeps only valid ones. Retrieval selectors (BM25, embeddings) plug into the same contract and are chosen by backend configuration only.
- Money is computed in code (Decimal); the model repeats tool values.
- Conversation memory is per session, in memory, bounded, and includes server-written records of the tool calls made in each earlier turn.
- Every paid call passes a budget ledger that reserves the worst case before dispatch and settles only provider-reported usage.

## Final configuration
Anthropic Claude Haiku 5.5 (adaptive thinking, effort medium), full manual, 5-minute prompt cache on the system prompt, at most 4 model calls / 8 tool calls / 90 s per turn; config `d9f674fcc08d`, frozen at commit `0dc5253`.

## Method
- 30 development scenarios (catalog, pricing, policy, continuity, scope, security; 2 perturbed: injected text in a product description and a catalog timeout), each with written pass criteria and critical failures.
- Runs go through the same service as the API and are reviewed by hand with one strict reading; a critical failure (for example an invented price or policy, a stacked discount, a claimed purchase or refund) fails the scenario regardless of the rest.
- 10 reserved validation cases, run once after the configuration was frozen (manifest with commit, config, data, rubric and validation-file hashes), on a separate budget.

## Development results
| Configuration | Prompt | Pass | Critical | Cost (30 scenarios) |
|---|---|---|---|---|
| Haiku 5.5, full manual | previous | 26/30 | 0 | US$0.096 |
| Haiku 5.5, full manual, 5-min cache | previous | 26/30 | 0 | US$0.028 |
| Haiku 5.5, BM25 top-4 sections | previous | 21/30 | 1 | US$0.055 |
| GPT-6 Luna, full manual | previous | 15/30 | 0 | US$0.011 |
| Haiku 5.5, embeddings top-4, no cache | revision 2 | 25/30 | 0 | US$0.061 |
| Haiku 5.5, BM25 top-4, no cache | revision 2 | 24/30 | 1 | US$0.059 |
| Haiku 5.5, full manual, no cache | revision 2 | 28/30 | 0 | US$0.107 |
| **Final:** Haiku 5.5, full manual, 5-min cache (two runs) | revision 2 | 27/30, 28/30 | 0, 0 | US$0.030, US$0.031 |

## Retrieval decision
- Embeddings cut the policy context to four sections, but they did worse than the full manual in this experiment. Answers missed facts when a needed section was not retrieved (contacts, delivery times; English queries retrieved 1 of 3 required groups), and one answer gave contact data that is not in the manual.
- BM25 was the cheapest of the controlled cache-off runs but produced one critical failure: it told a customer to disregard exchange exclusions that the manual does state.
- The full manual had the strongest observed quality and no critical failure in any run.
- The 5-minute prompt cache cut the cost of full context by about 71% (US$0.031 vs US$0.107 for the same 30 scenarios) with no observed quality change.
- Full manual + cache was therefore selected for this small, static manual. This is one run per retrieval policy, so it is an engineering decision on observed differences, not a statistical result or a general claim about retrieval. Both retrieval options remain in the code as evaluated alternatives.

## Reserved validation
Frozen configuration, run once: 10/10 cases and 21/21 customer turns executed, no divergence from the freeze, **9/10 passed, 0 critical failures**, US$0.014, 94 s. The failure (VAL-008) handled a damaged delivery and the order-lookup boundary correctly but omitted the manual's transport-insurance statement. It is kept as a known limitation, and nothing was tuned after the run. 9/10 on a small suite is supporting evidence for the frozen configuration, not proof of correctness or a production SLA.

Method limitation: the reserved cases were written by the AI coding assistant after the configuration was selected, with knowledge of the project. They were not blind or independent, and the same reviewer graded them. They were frozen before the run and the candidate was not changed after it.

## Examples
Five conversations from a real development run of the final configuration, unchanged and in Portuguese as executed: `examples/conversations.md`. They cover a catalog search with a recommendation, price and installments across turns, a return under the policy's conditions, installments where the manual's two rules disagree, and requests the assistant cannot perform (purchase, reservation, cancellation, refund).

## Limitations
- Small suites (30 known development scenarios, 10 reserved cases run once): observed differences, not statistics.
- Known non-critical gaps: metro delivery time, tracking-code format, transport insurance on a damaged delivery.
- Static catalog snapshot, not live stock; contradictory manual entries (two phone numbers, two e-mail spellings, two installment rules) are reported, not resolved.
- The cache saves cost only when calls arrive within 5 minutes; sparse chat traffic pays the full prompt more often.
- In-memory sessions, single worker; no orders, customer data, purchases, handoff, authentication or deployment.
- Prototype: not a production service.

## With more time
- An independently authored, larger reserved set and repeated runs to estimate variance.
- Fix the known omissions with general instruction changes, re-measured on new cases.
- Hybrid retrieval only if the manual grows past what full context handles well.
- Order status with authentication, a human handoff and persistent sessions.

## AI assistance
Planned with ChatGPT and implemented with Claude Code under the owner's direction. Claude Code also wrote the reserved validation cases and the hand reviews of the runs. The owner reviewed every change, made the decisions and made all commits.
