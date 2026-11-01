# Evaluation and API budget contract

## Dataset and isolation
Create 30 development scenarios from assignment/store data: exact catalog facts, absent stock, promotion/PIX, installments, policy exceptions, mixed queries, follow-ups, out-of-scope and adversarial attempts.
Matheus controls 10 reserved scenarios OUTSIDE the development tree. Their input/answers must not be shown to the prompt-tuning agent. Create a schema/template using development examples only. Final evaluation can be run by Matheus and return aggregate/approved diagnostics. If holdout cases are used to tune, disclose contamination and replace the holdout before claiming untouched validation.
Each case defines expected business outcome, allowed/required tool behavior where appropriate, evidence and prohibited claims. Accept valid paraphrases; don't compare every response to a single exact string. Model judges complement deterministic checks and human review, never become unquestioned truth.

## Staged experiment (maximum plan, not quota)
1. Offline unit/integration and very small real smoke pilot; estimate spend/duration.
2. Up to 3 models ×30 development cases ×2 trials =180 scenario executions using full manual baseline.
3. Two promising models ×2 additional retrieval variants ×30×2 =240 additional executions. Reuse their baseline results. Retrieval variants use comparable sections/metadata and recorded top-k/thresholds.
4. Freeze up to two configurations. Owner runs 10 reserved cases ×2×3 =60 executions.
Up to480 scenarios, often multiple API calls each. Reduce rounds when budget/time/evidence warrants. No obligation to exhaust allocation.

## Comparable runs
Same data, business rules and tool semantics. Isolated session per scenario; alternate run order; control concurrency/retries. Record prompt hash, code revision or uncommitted diff state, model identifier/configuration, adapter/SDK version, retrieval config and date/pricing basis.
Use provider-specific effort settings explicitly; same label across vendors does not imply equal compute. Do not conflate billed reasoning with visible answer tokens. Account for tool/embedding latency. No sequential cache advantages silently favoring one candidate.
For Qwen: actual context capacity and CPU/GPU split must be measured. Full manual + tools + history + response reserve must fit; do not silently drop inputs. If hardware prevents a comparable run, report that constraint and any changed configuration.

## Metrics
Per-category correctness, policy/tool adherence, unsafe output/access attempts, clarification and out-of-scope behavior, repetitions all passing, failures/retries/tool count, first useful customer text and total turn/conversation duration. Separate loading/warm inference, embeddings/query/indexing, caching and rate-limit delays. Small-sample p50/p95 are exploratory.
Measure actual total cost and cost per approved scenario (including failed-attempt spend in numerator). No real production deflection/CSAT claims from synthetic tests. Safety/correctness gates come before economic ranking.

## Ledger REQUIRED before any paid batch
US$10 hard project ceiling across Anthropic+OpenAI+embeddings+paid judges+retries. Development ceilingUS$7, reserved validationUS$2, marginUS$1 not automatically spendable. These are MAXIMA.
Implement a shared ledger/reservation gate, initially sequential, with conservative upper bounds, finite output/tool/retry limits, actual usage reconciliation and durable spend records. Unknown usage/timeouts retain conservative reservation until resolved. Cache savings only when provider usage confirms. Fail closed on unknown prices or missing ledger.
No live default, no paid CI. Require explicit live switch AND owner-approved bounded batch through the ledger. Never let an environment flag alone count as budget enforcement. Prevent calling providers around the gate; test that adapters can't bypass it.
Current harness does NOT implement this ledger or grant paid execution. Owner must confirm Claude Code auth/billing first. The included shell guard is not a network spending control.

## Evidence storage
Sanitize logs; don't export secrets, customer PII or reserved-case answers. Save development reports with real measurements, model-config metadata and trace IDs where appropriate. A mock/demo report must visibly say SIMULATED. Never reconstruct supposedly observed tool results from the expected labels.
