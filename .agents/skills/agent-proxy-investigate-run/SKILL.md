---
name: agent-proxy-investigate-run
description: Investigate what happened to an Agent Proxy trajectory, repository run, issue, or workflow by joining governed reliability, cost, policy, evaluation, dossier, freshness, and trace evidence. Use for failures, partial runs, retries, fallbacks, interventions, or evidence-backed status questions.
---

# Investigate an Agent Proxy run

Use the repository-owned read-only query helper. It joins metadata-only operational views without granting authority or exposing raw bodies.

1. Run `ward exec trajectory-query -- investigate` with one or more exact `--repository`, `--issue`, `--workflow`, or `--trajectory` filters.
2. Report the matched trajectory count, view freshness, missing evidence, reliability, cost and latency, policy, evaluation, dossier, and trace joins.
3. Treat `may_authorize: false`, partial reasons, absent dossiers, and reconstruction limits as hard boundaries. Hand trace ids to an approved observability surface when deeper diagnosis is needed.

For command behavior and flags, run `ward exec trajectory-query -- --help`.
