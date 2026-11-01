---
paths:
  - "backend/**"
---
# Backend rules
- Keep APIs, agent loop, tool/business logic and database access separate without framework proliferation.
- Validate boundary schemas; parameterized SQL; Decimal money; read-only product tools.
- Session identity is server-owned, not an LLM argument granting access. No private-order/customer endpoints in v1.
- All actual provider calls, including retries/embedding queries, must pass the shared budget gate before live mode is enabled.
- Tests default offline; errors/usage logs must not expose credentials, customer data or reserved evaluation content.
- Bound timeouts, tool rounds, output tokens and retries. Cancellation/error must not claim an action completed.
