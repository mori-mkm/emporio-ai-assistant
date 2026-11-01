# Empório AI Assistant — Claude entry point

Read and follow @AGENTS.md. It is the canonical project instruction file; do not maintain a competing copy of its rules here.

Project: the Artefact AI Engineer technical case. Build incrementally on Windows with Python/FastAPI and React/TypeScript. Start with Haiku and a complete-policy-context baseline. The product has ONE agent; development reviewers are optional.

Before editing, read `docs/architecture.md`, `docs/development.md`, `docs/progress.md`, and the selected EMP issue's acceptance criteria. Load other documents only when relevant. Inspect the original case PDF before treating this plan as the assignment itself.

Offline harness check:
```powershell
python scripts/verify.py --mode harness
```
After the application exists:
```powershell
python scripts/verify.py --mode app
```

Do not run paid calls in the first session. Do not commit, push, submit the case, or modify the Closer project. Never print credentials. Matheus owns final decisions, review and Git commits.

Start by auditing the existing folder, validating the harness, and proposing the first small implementation step. Do not build the whole plan in one session.
