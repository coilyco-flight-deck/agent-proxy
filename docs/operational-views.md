# Operational views and Ward dossier inputs

Agent Proxy exposes internal cold-path views under
`/v1/trajectory/views/<name>` and evidence-only dossier inputs under
`/v1/trajectory/dossiers/<trajectory-id>`.

## Read-only query helper

Use the repository-owned helper for deterministic filtering and cross-view joins:

```text
ward exec trajectory-query -- --help
ward exec trajectory-query -- investigate --issue owner/repository#42
ward exec trajectory-query -- harness-fit --harness codex --model logical/model
ward exec trajectory-query -- skill-use --skill coding-python --role engineer
```

`investigate` accepts exact repository, issue, workflow, and trajectory filters,
then joins reliability, cost and latency, policy, evaluation, and dossier
evidence by trajectory id. `harness-fit` filters the existing observational
aggregate by harness or model. It does not add a time window or repository
dimension that the underlying view does not contain. `skill-use` filters the
skill aggregate by skill, role, harness, or model.

The helper defaults to `PROXY_BASE_URL` or `http://127.0.0.1:8080`. It emits
JSON on stdout, copies no response bodies into errors, and never mutates Agent
Proxy.

## Query contracts

Versioned contracts cover:

* **reliability** - completion, partial reasons, retries, fallbacks, human
  intervention, and late-event counts
* **cost_latency** - models, providers, token use, latency, and cost by currency
* **policy** - observed allow, deny, review, and defer decisions
* **evaluation** - active labels, disagreement, supersession, and late evidence
* **harness_fit** - comparative completion, retry, fallback, latency, and cost by
  harness and model
* **skill_fit** - observed skill selection and use against completion, retry,
  fallback, intervention, and evaluation evidence, by skill, role, harness, and
  model. Selection and observed use are separate facts and are never merged. A
  selected skill with no matching Ward observation keeps a row flagged
  `selected_without_observed_use`, since absent evidence and evidence of absence
  are different claims.

Every trajectory row carries repository, issue, workflow, trace, and span joins.
Trace ids join the durable evidence to OTLP and SigNoz. Those operational
systems do not become the trajectory store.

## Ward boundary

`agentproxy.ward.dossier-input` version `1.0` contains reliability, evaluation,
governance correlation, and trace evidence. Its `may_authorize` field is always
false. Ward alone decides authorization, execution, lifecycle, recovery, and
governance.

## Access and redaction

Access filtering happens before row construction. The unauthenticated internal
HTTP surface returns only `internal` rows. Restricted consumers must construct
an explicitly authorized `AccessPolicy` inside a controlled deployment
boundary. Views never contain prompt, response, tool, annotation, or
environment bodies.

## Freshness and recovery

Every view publishes:

* generation time
* latest source materialization time
* complete-through watermark
* age in seconds
* the immutable raw ledger as its backfill source
* reconstruction limits

The view pipeline replays retained raw evidence, appends changed materialization
revisions, and reassembles evaluation records. It cannot reconstruct events or
bodies that were never captured or are outside the selected access tier.
