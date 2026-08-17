---
name: agent-proxy-evaluate-skill-use
description: Evaluate observed Agent Proxy skill adoption using selection claims, observed use counts, trajectory count, completion, retries, fallbacks, evaluation labels, freshness, and source evidence. Use when reviewing whether a skill is reaching the runs it was selected for and what outcomes followed, without claiming the skill caused them.
---

# Evaluate Agent Proxy skill use

Use the repository-owned read-only query helper to inspect the governed `skill_fit` view.

1. Run `just trajectory-query skill-use` with optional exact `--skill`, `--role`, `--harness`, and `--model` filters.
2. Read `observed_use` and `observed_use_count` as Ward observations, and read the selection claim separately. They are different facts and the view never merges them.
3. Treat `selected_without_observed_use` as missing evidence, not as proof the skill went unused. An adapter that never ran produces the same shape as a skill nobody invoked.
4. Compare completion rate only beside trajectory count, retries, fallbacks, human interventions, evaluation labels, access tier, and freshness.
5. Report unresolved evaluation disagreement rather than resolving it.
6. State that the result is observational. It is not causal proof that a skill produced an outcome, and it never authorizes a Ward action.

For command behavior and flags, run `just trajectory-query --help`.
