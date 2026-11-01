# Architecture baseline and decision register

Status: approved direction. Implemented so far: health API (EMP-4), offline data preparation (EMP-5, `docs/data.md`), development scenarios (EMP-6), budget ledger + runner (EMP-7, `docs/evaluation.md`), read-only tools with deterministic commercial rules (EMP-8, `docs/tools.md`), the single Haiku agent with session API (EMP-9) and the chat UI (EMP-10). The real Haiku path is tested only offline with a fake client. Original assignment wins over a mistaken paraphrase here; document mismatches.

## Product and boundaries
Empório da Música support assistant. Reduce repetitive inquiries while keeping stock, price and policy answers accurate. Store tone: welcoming, informal, professional and knowledgeable, without pressure or invented claims.
Single Python agent → allowlisted tools → read-only catalog. FastAPI is the application boundary; React/TypeScript is the interface. Session context supports follow-up questions. No private customers/orders or operational writes in v1.

## Implemented flow (EMP-9/10)
Browser (`frontend/src/App.tsx`, `api.ts`) -> FastAPI (`main.py`) -> `AssistantService` (`assistant.py`: sessions, request ids, error mapping) -> `Agent` (`agent.py`: one bounded loop) -> generator (`anthropic_adapter.py` live, or `SimulatedGenerator`) and `tools.call_tool` (EMP-8).
EMP-11 (offline): generators share the neutral contract in `generation.py` (tool calls/results, final text, stop, usage diagnostics); each adapter converts messages itself and keeps provider items as private state. `openai_adapter.py` (GPT-6 Luna, Responses API, ledger) and `ollama_adapter.py` (qwen3:8b, loopback `/api/chat`, no ledger) are selected only by backend env or runner flags; no routing or fallback.

- **Context per call:** system = trusted instructions + the full manual of the session's data generation, rendered as `<section id title pages>` blocks marked as data. Messages = session history (text only) + this turn's protocol blocks. Tool results go only in `tool_result` blocks of a user message, never in the system prompt. Thinking blocks are passed back unchanged inside a turn and never shown.
- **Loop:** model -> if `tool_use`, run each requested tool sequentially through the allowlist with strict argument validation, answer every id with a `tool_result` (`is_error` on failure, the same tool+arguments twice in a turn returns `duplicate_call`) -> repeat until a final answer or a limit. `refusal`, `max_tokens`, context exhaustion, an empty answer or any other stop reason return a fixed Portuguese message; no fallback model, no retry.
- **Limits (backend only, `AgentConfig` / `GenerationSettings`):** 5 model calls and 8 tool calls per turn, 90 s per turn, 60 s per call, 2 000 characters per message, 24 history messages, 4 096 output tokens (thinking included), 90 000-token prompt bound (below the 100k price tier; never truncated: the turn is refused and the customer is asked to start a new conversation), 200 active sessions, 30-minute idle expiry.
- **Model configuration:** `claude-haiku-5-5`, adaptive thinking with `display: omitted`, effort `medium` stated explicitly, `tool_choice: auto`, no temperature/top_p/top_k, no caching, batch, server tools, `inference_geo` or priority tier. SDK `max_retries=0` and explicit timeout. Prompt size is bounded offline (UTF-8 bytes + 286 tool-system tokens + 1 000 margin + one output budget per earlier thinking turn); the token-counting API is not called.
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

## Decisions remaining (experiments, not architecture debates every session)
1. Final generation model/configuration: Haiku vs Luna vs Qwen3.
2. Policy access: full context vs BM25 vs semantic retrieval. Hybrid/reranker only if complementary failures justify it.
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
