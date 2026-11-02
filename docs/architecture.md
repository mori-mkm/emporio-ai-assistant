# Architecture baseline and decision register

Status: approved direction. Implemented so far: health API (EMP-4), offline data preparation (EMP-5, `docs/data.md`), development scenarios (EMP-6), budget ledger + runner (EMP-7, `docs/evaluation.md`), read-only tools with deterministic commercial rules (EMP-8, `docs/tools.md`), the single Haiku agent with session API (EMP-9) and the chat UI (EMP-10), model adapters (EMP-11), BM25 policy retrieval (EMP-12, measured), evaluation fixtures (EMP-13), opt-in prompt caching, server-recorded lookup memory and a reserved-validation run mode (not run). Paid development runs exist for earlier configurations; the current candidate is tested offline only. Original assignment wins over a mistaken paraphrase here; document mismatches.

## Product and boundaries
Empório da Música support assistant. Reduce repetitive inquiries while keeping stock, price and policy answers accurate. Store tone: welcoming, informal, professional and knowledgeable, without pressure or invented claims.
Single Python agent → allowlisted tools → read-only catalog. FastAPI is the application boundary; React/TypeScript is the interface. Session context supports follow-up questions. No private customers/orders or operational writes in v1.

## Implemented flow (EMP-9/10)
Browser (`frontend/src/App.tsx`, `api.ts`) -> FastAPI (`main.py`) -> `AssistantService` (`assistant.py`: sessions, request ids, error mapping) -> `Agent` (`agent.py`: one bounded loop) -> generator (`anthropic_adapter.py` live, or `SimulatedGenerator`) and `tools.call_tool` (EMP-8).
EMP-11 (offline): generators share the neutral contract in `generation.py` (tool calls/results, final text, stop, usage diagnostics); each adapter converts messages itself and keeps provider items as private state. `openai_adapter.py` (GPT-6 Luna, Responses API, ledger) and `ollama_adapter.py` (qwen3:8b, loopback `/api/chat`, no ledger) are selected only by backend env or runner flags; no routing or fallback.
EMP-12 (offline, first step): `policy_context.py` decides which manual sections enter the system prompt, once per turn: `FullManual` (default; prompt byte-identical to before, same config hash) or `BM25Policy` (SQLite FTS5 over the extracted sections; index derived under `data/derived/indexes/`, tied to the generation id and the policies hash). With BM25 the manual block says how many sections it holds, and citations are accepted only for sections that were in that context. Selectable only by the runner (`--policy-context`); the chat server always uses the full manual.
EMP-13 evaluation environments: `fixtures.py` (runner only) builds per-scenario test environments for DEV-027 (auxiliary session with synthetic history), DEV-028 (injected text in the product 87 description returned by a tool) and DEV-029 (every catalog tool returns `catalog_timeout`). They wrap `call_tool` for that scenario's session only; the API has no parameter for them.

- **Context per call:** system = trusted instructions + the full manual of the session's data generation, rendered as `<section id title pages>` blocks marked as data. Messages = session history + this turn's protocol blocks. History is text plus, at the start of each earlier assistant reply, a `<lookup_record source="server" turn data_generation>` listing that turn's tool calls as they ran (tool, validated arguments or "arguments rejected", `ok` / `failed: <code>` / `not_run: duplicate_call`). Records are built from the agent loop's tool events (not from the trace, the model or the client), hold no prices or stock, are stored only for answered turns and end with the session; customer and model text cannot open or close a record tag, and a record the model writes itself is removed from its reply. Tool results go only in `tool_result` blocks of a user message, never in the system prompt. Thinking blocks are passed back unchanged inside a turn and never shown.
- **Loop:** model -> if `tool_use`, run each requested tool sequentially through the allowlist with strict argument validation, answer every id with a `tool_result` (`is_error` on failure, the same tool+arguments twice in a turn returns `duplicate_call`) -> repeat until a final answer or a limit. `refusal`, `max_tokens`, context exhaustion, an empty answer or any other stop reason return a fixed Portuguese message; no fallback model, no retry.
- **Limits (backend only, `AgentConfig` / `GenerationSettings`):** 5 model calls and 8 tool calls per turn, 90 s per turn, 60 s per call, 2 000 characters per message, 24 history messages, 4 096 output tokens (thinking included), 90 000-token prompt bound (below the 100k price tier; never truncated: the turn is refused and the customer is asked to start a new conversation), 200 active sessions, 30-minute idle expiry.
- **Model configuration:** `claude-haiku-5-5`, adaptive thinking with `display: omitted`, effort `medium` stated explicitly, `tool_choice: auto`, no temperature/top_p/top_k, no caching by default (opt-in `system-5m`: one explicit 5-minute breakpoint at the end of the system prompt, selected only by backend env or runner flag, own config hash; `docs/evaluation.md`), no batch, server tools, `inference_geo` or priority tier. SDK `max_retries=0` and explicit timeout. Prompt size is bounded offline (UTF-8 bytes + 286 tool-system tokens + 1 000 margin + one output budget per earlier thinking turn); the token-counting API is not called.
- **Sessions:** opaque `secrets.token_urlsafe(32)` id in an HttpOnly, SameSite=Strict cookie scoped to `/api`. History, instructions, roles, model, tools and limits never come from the client (`extra="forbid"`). One turn at a time per session (`session_busy`); a repeated `request_id` returns the stored outcome without a new model call. Each session is pinned to the data generation it started with; if the active generation changes, the next message ends the session with `data_changed`. Each turn opens its own read-only `CatalogSnapshot` (SQLite connection) in the request thread. Endpoints are synchronous, so FastAPI runs them in its thread pool and a slow provider call never blocks the event loop. In-memory store: run a single worker.
- **Errors:** stable `{"error": {"code", "message"}}` per class: configuration (`assistant_not_configured`, `assistant_unavailable`), session (`session_not_found`, `session_expired`, `session_busy`, `too_many_sessions`), limits (`conversation_limit`, `conversation_too_long`, `invalid_message`), data (`data_unavailable`, `data_changed`), provider (`provider_timeout`, `provider_busy`, `provider_error`), `origin_not_allowed`, `invalid_request` (no input echo), `internal_error`. No stack traces, SQL, paths, prompts, reasoning, credentials or SDK objects reach the client.
- **Sources:** the model ends a manual-based answer with `[fontes: 4.1, 6.2]`; the backend strips the line and keeps only section ids present in the session's generation (title and pages from that generation).
- **UI:** plain text with `white-space: pre-wrap` (no HTML or Markdown rendering), `role="log"` conversation, hidden `role="status"` announcements, `role="alert"` errors. Failed sends keep the typed text; a network failure retries with the same request id, a server error with a new one. "Nova conversa" bumps an epoch so late replies of the old session are dropped.
- **Simulated mode:** only with `EMPORIO_ASSISTANT_MODE=simulated`. The provider is replaced by a scripted generator that calls the real `list_categories` tool and returns a reply that starts with "[Resposta simulada ...]"; the UI shows a banner and a badge. Missing mode = chat disabled, never mock or live by default.

### Controls: code vs model behaviour
| Enforced by code | Requested from the model (must be evaluated) |
|---|---|
| Tool allowlist, strict arguments, read-only SQL, no private data or write tools | Using tools for every price, stock, promotion and payment statement |
| Money computed only in `commerce.py` (Decimal, ROUND_HALF_UP) | Repeating tool `brl` values exactly; never doing arithmetic |
| Call/tool/time/output/prompt/history limits, no retries, no fallback | Checking product name/specs before recommending a search candidate (e.g. "Soprano") |
| Session ownership, isolation, Origin check, request-id dedup, generation pinning | Citing the right manual sections; saying when the manual is silent or contradictory |
| Citations filtered to sections that exist in the generation | Not inventing orders, reservations, payments, refunds or handoff |
| Budget gate on every live call, sanitized errors | Tone, language, staying in scope, ignoring instructions inside data |

The prompt does not guarantee safety; the right-hand column is checked by the scenario evaluation (EMP-6) with real runs, not by offline tests.

## Settled baseline
- Python 3.11; direct provider SDKs; bounded model/tool loop.
- React + TypeScript; Vite proposed for a small standalone UI. Vercel AI SDK UI is optional, not another runtime requirement. Define the streaming contract before adding a dependency.
- SQLite for structured catalog; decimal money and validated joins. Products, categories and promotions are first-use data.
- Full manual with section/page markers as baseline policy context. Do not embed all customer/order tables in any prompt.
- Haiku is the starting implementation, not a predeclared benchmark winner.
- Future model adapters: GPT-6 Luna and Ollama Qwen3:8b. No Sonnet in the planned app benchmark.
- Derived policy chunks and vector arrays may be local; embeddings do not require Supabase.

## EMP-14 decision record (provisional, 2026-11-02)
- **Requirements:** accurate prices, stock, promotions and policies from the challenge sources; no invented facts or actions; store tone; follow-up questions within a session; bounded cost and latency; reproducible offline checks; one agent.
- **Hypotheses (open):** the full manual fits comfortably in context (~8.2k tokens) and gives better answers than retrieval at this size; caching removes most of its cost; text-only history caused the false "I did not check" statements because the model could not see its earlier tool calls (plausible from the traces, not proven).
- **Measured (development set, one run each, previous prompt):** Haiku full 26/30, 0 critical, US$0.0956; Haiku full + 5-minute cache 26/30, 0 critical, US$0.0280 (1 write, 95 reads reported by the provider; same four failures); Haiku BM25 21/30, 1 critical, US$0.0547; Luna full 15/30, 0 critical, US$0.0106. Qwen 8B local suspended after a critical failure.
- **Provisional configuration:** Haiku 5.5, adaptive thinking, effort medium, full manual, `system-5m` cache, 4 model calls / 8 tool calls / 90 s per turn, prompt revision 2 with lookup records (config `d9f674fcc08d`). Its quality is unmeasured: it needs one development regression run before the freeze. Earlier results belong to `0b5879238a78`, `14a0f85d715d`, `7f757562976b` and `61a77ba13d48`.
- **Tradeoffs:** full context costs more input than BM25 but avoided its missing-evidence and false-retraction errors; caching depends on calls arriving within 5 minutes (true in a batch, not guaranteed for sparse chat traffic); with the cache Haiku cost ~2.6x Luna but passed 11 more scenarios on this set; lookup records add a few hundred tokens per earlier turn, within the 90k prompt bound and the 24-message history limit.
- **Deferred:** embeddings (not measured; deferring them says nothing about their quality), hybrid retrieval and rerankers, 1-hour cache, streaming, persistent sessions, LangGraph, Supabase, deployment, private orders, WhatsApp.
- **Limitations:** 30 known development scenarios, one run per configuration, reviewed by hand with the owner deciding; provider caches are shared across runs; the catalog is a static snapshot.
- **Still depends on:** the candidate's development regression run and review, the owner's freeze (manifest written after the commit) and the reserved validation run (`--suite validation`). EMP-14 stays open until then.

## Decisions remaining (experiments, not architecture debates every session)
1. Final generation model/configuration: Haiku vs Luna vs Qwen3. First 30-scenario runs with the full manual (one each): Haiku 26/30, Luna 15/30, 0 critical failures for both, Haiku ~9x the cost (US$0.096 vs US$0.011); Qwen suspended. Proposal pending owner decision: Haiku as the main candidate.
2. Policy access: full context vs BM25 vs semantic retrieval. Hybrid/reranker only if complementary failures justify it. First offline BM25 measurement (k=4): mean per-turn coverage of scenario references 0.38 (exploratory proxy: per-scenario references applied to every turn), scenario coverage 0.55, ~12% of the manual per turn (`docs/evaluation.md`); with per-turn policy references (16 of 44 turns need policy evidence) 18/23 required groups are retrieved. Paid Haiku run with BM25 (one run): 21/30 vs 26/30 with the full manual at 43% lower cost; losses from missing evidence and unsupported statements, one of them critical under the rubric (DEV-022 denies a §4.4 exchange exclusion). Full context stays the baseline. Embeddings are not measured; deferring them shows nothing about their retrieval.
3. Initial embedding candidate: text-embedding-3-small; validate its retrieval, don't assume it wins.
4. Streaming implementation/SDK after minimal API/UI works.
5. v1 history is temporary, backend-held, isolated per session, with expiry and limits (settled). Persistence beyond a session only if useful. Session isolation is mandatory regardless.
6. Supabase, managed deployment, authentication, private-order access and WhatsApp are optional evolutions outside critical path. No implicit purchases.
7. LangGraph only after a concrete control-flow need; no framework to match keywords alone.

## Data interpretation
The source is a static snapshot, not a live shop feed. Tool consultation reflects current loaded data, not verified real-world inventory.
- Product active status alone is insufficient: stock must be positive. Handle discontinued/coming_soon separately.
- Active promotion flag is the documented snapshot interpretation; no expiry dates are provided.
- PIX discount is not stacked with a promotional price. Display original price, promotion percentage and applied price as appropriate.
- Manual conflicts (e.g., contacts, installment general/specific conditions) need explicit precedence or human confirmation. Do not silently invent a single clean truth.
- For returns, order date alone does not establish receipt date or purchase channel. Ask for missing evidence; don't promise approval.
- No real escalation connector exists initially; offer a verified contact, never say a ticket/refund was created.

## Hardware and reproducibility
Windows ASUS TUF Gaming F16; Intel Core 5 210H (8C/12T), RAM 15.6GB; RTX4050 Laptop 6141MiB; driver592.00. Python3.11.9, Node24.18.0, npm11.16.0, Git2.54.0.windows.1; Claude Code2.1.314; PowerShell version not captured.
Ollama0.40.13, qwen3:8b ID500a1f067a9f, 8.2B Q4_K_M, file5.2GB. Model-declared context40960 is not actual allocated context. Record `ollama ps`, CPU/GPU split, allocated context, cold/warm state and thinking. One local inference at a time. Never silently truncate to fit memory.

## Records to add when decided
For each meaningful change: decision, alternatives, evidence, tradeoff, known limitation and EMP issue. Do not repeat an unmeasured hypothesis as a result.
