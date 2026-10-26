# AI answer diagnostics

`questions.v1.json` is a frozen 74-case, curated diagnostic corpus written before
candidate scores. SHA256: `5a1abd573b638960291e1634f03015a59e9437e4c5cdc784ca47f9ca4448a087`.
It is not a representative production sample or a legal/factual certification.

From a configured checkout (no credentials required for offline):

```sh
.venv/bin/python tools/submission/answer_quality_eval.py --output /tmp/answer-quality.json
```

`--source-root /path/to/old/checkout` isolates imports and scenario for baseline
comparison while retaining this frozen corpus. Run comparisons in fresh processes.
Retrieval checks actual pre-model candidates. `ideal_selection_assembly_NOT_model_output`
checks assembly using explicitly supplied oracle IDs; it never measures real
model selection. Source URL concordance does not check HTTP availability or
current law. Emergency and nonurgent denominators are reported separately.

One genuine oracle/requirements conflict was found: q001 forbids rental search
for personal state-housing eligibility, while the user's explicit requirement
preserves that step. The frozen corpus and raw score were not edited. See
`requirements-adjudication.v1.json` for the exact requirement and separately
reported corrected metrics. Do not call an adjudicated score the raw frozen score.

Real-provider evaluation is opt-in (`--live`, `ANSWER_QUALITY_ALLOW_PROVIDER=1`,
existing valid provider configuration and enabled `PLANNER_AI_ENABLED`/`LLM_ENABLED`).
Do not add secrets to this repository. No live evaluation ran during this revision.
Read the private deployment runbook before running against the intended provider;
MAX messages/VPS are not part of this standalone runner.
