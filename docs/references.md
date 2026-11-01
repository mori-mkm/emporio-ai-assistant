# Primary references and adaptation notes

Reviewed for this package on 2026-11-01. Reference examples are not universal guarantees, and their simulated numbers are not this project's measurements. Do not copy large proprietary passages into the repo.

- Anthropic, Effective harnesses for long-running agents (Nov26,2025): https://www.anthropic.com/research/effective-harnesses-for-long-running-agents . Apply incremental work, explicit feature acceptance, progress handoffs and browser verification. Adapt automatic Git commits to Matheus's manual-commit workflow.
- Anthropic, Harness design for long-running application development (Mar24,2026): https://www.anthropic.com/research/harness-design-long-running-apps . Apply targeted evaluation; do not copy ambitious autonomous/multi-agent scope or hours of visual redesign loops.
- OpenAI, Harness engineering (Feb11,2026): https://openai.com/index/harness-engineering/ . Apply short map-like instructions, repository knowledge and enforceable invariants. Do not copy autonomous merges or reduced gates from its different operating context.
- Claude Code best practices: https://code.claude.com/docs/en/best-practices . Give verifiable checks, scope context, distinguish instructions from deterministic controls.
- Memory: https://code.claude.com/docs/en/memory . CLAUDE.md imports canonical AGENTS.md; inherited/user instructions must be reviewed. File guidance is not isolation.
- Permissions: https://code.claude.com/docs/en/permissions . Narrow tool access; read/edit rules do not cover every indirect subprocess.
- Hooks: https://code.claude.com/docs/en/hooks . Exec form with project placeholder; onFailure:block is documented for versions>=2.1.295. This package targets installed2.1.314. Hook is best-effort accidental-action prevention, not a full sandbox.
- Subagents: https://code.claude.com/docs/en/sub-agents . Project-scoped read-only reviewer only when useful.
- Testing: https://fastapi.tiangolo.com/tutorial/testing/ ; https://playwright.dev/docs/best-practices . Offline contracts plus user-visible browser behavior.
- Product/eval foundations: https://claude.com/resources/articles/the-anatomy-of-effective-commerce-agents ; https://www.anthropic.com/research/demystifying-evals-for-ai-agents ; https://developers.openai.com/cookbook/examples/agent_optimization/optimizing_agents_for_cost_and_quality . The OpenAI support cookbook's dry-run metrics are simulated, not empirical predictions for this case.
- Retrieval evidence: https://www.anthropic.com/research/contextual-retrieval ; https://proceedings.mlr.press/v267/li25dv.html . Compare methods on our data; no universal RAG winner assumed.
