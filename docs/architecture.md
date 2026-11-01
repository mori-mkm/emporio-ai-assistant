# Architecture baseline and decision register

Status: approved direction, application NOT implemented. Original assignment wins over a mistaken paraphrase here; document mismatches.

## Product and boundaries
Empório da Música support assistant. Reduce repetitive inquiries while keeping stock, price and policy answers accurate. Store tone: welcoming, informal, professional and knowledgeable, without pressure or invented claims.
Single Python agent → allowlisted tools → read-only catalog. FastAPI is the application boundary; React/TypeScript is the interface. Session context supports follow-up questions. No private customers/orders or operational writes in v1.

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
5. History persistence beyond a session only if useful. Session isolation is mandatory regardless.
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
