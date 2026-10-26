# Owner answer evaluation stand — implementation plan

Goal: implement the approved in-chat design (owner «го»), extending existing diagnostics.
Architecture: standalone Python snapshots and persistent batch budget; Promptfoo only replays snapshots, never makes paid calls. Static HTML allows editor calibration without a server. Product code/publication/VPS unchanged.
Spec: SPEC.md beside this plan. Execution: inline, using writing-plans/executing-plans and test-driven verification. Existing checkout clean at4514a42; keep existing branch/stashes intact, no unnecessary worktree.

Constraints: owner is editor; concise concrete facts first, detail links; six exact owner questions plus tagged development paraphrases; frozen74 unchanged. Overall new pilot ceiling50RUB including judge/retries. No configured provider means NOT_RUN, not mocked success.

Review focus: stored/malformed replies; errors omitted from denominator; oracle credit confused with model; aggregate budget/retries across restarts; HTML/link injection.

- [x] Task1: owner dataset and concreteness checks in tools/submission/answer_eval_stand.py. Tests first: screenshot fails despite valid links; exact negation/conditions preserved; unavailable not scored as good; non-specific ambiguity not guessed.
- [x] Task2: eval_budget.py and serial live pilot. Tests first: retry-reservation cap; reused-ledger aggregate cap; wrong provider/model rejected; no credentials printed; missing usage never refunds reservation. Offline prompt-size capture must never call network. Run starts offline, requires --live and explicit allowance for paid path.
- [x] Task3: promptfoo_bridge.py, generated config and escaped editor report. Tests first: unknown snapshots error; critical failure cannot be averaged away; HTML unsafe URL/markup rejected; replay has no provider calls. Pin Promptfoo separately from production dependencies and run actual offline CLI.
- [x] Verification: existing evaluator tests plus targeted stand/budget tests; npmtest; frozen74 offline; actual Promptfoo replay; source diff and human-readable report. Self-review trace all criteria/budget/source labels. Package fullsource/history/results and compatible rollback in authorized privatebranch; remoteSHA verification. No publicpush/deploy/MAX.

Ruling: prior explicit instruction to complete authorized work and accepted design supersedes skill's repeated plan-approval handoff; proceed without another permission round.
Ruling: no new product behavior change; stand diagnoses weak current answers rather than tuning them and calling the same dataset independent.

Verification result:195profilePython checks passed (including19stand),3Nodefiles passed; frozen74hash/67of67context unchanged; actualPromptfoo30replays0errors with expected quality failures/NOT_RUN. Chromium390px light/dark restore/export passed. Independent review closed3materialfindings. LiveNOT_RUN; actualspend0. Privatebackup details recorded in privatepackage aftersourcecommit.
