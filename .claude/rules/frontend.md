---
paths:
  - "frontend/**"
---
# Frontend rules
- React/TypeScript, accessible chat, explicit loading/error/new-conversation states.
- Use FastAPI contracts. No provider keys, DB credentials or direct model calls in client code; no VITE_ secret exposure.
- Render model output safely; never execute returned HTML/scripts or trust links/actions as authorization.
- Keep the customer flow simple. Benchmark dashboards and internal technical details are not requirements of the customer UI.
- Test visible behavior and session isolation. A successful build alone is not proof of end-to-end functionality.
