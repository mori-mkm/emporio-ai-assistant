---
paths:
  - "evals/**"
  - "backend/**/eval*"
  - "tests/**"
---
# Evaluation rules
- Read docs/evaluation.md before experiments. Never access owner-held validation cases while tuning.
- Offline mocks and real API results must be distinctly labeled. No paid call in the default test command or CI.
- Do not change assertions/expected values to hide a bug. Real rubric changes need explanation and owner review.
- Preserve original results; changes in prompts/model/data/settings require new identifiable runs.
- Record actual paid usage including retries/unknown outcomes conservatively. Stop below the authorized cap, not after overshooting it.
