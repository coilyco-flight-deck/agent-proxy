# Operational views and Ward dossier inputs

Agent Proxy exposes internal cold-path views under
`/v1/trajectory/views/<name>` and evidence-only dossier inputs under
`/v1/trajectory/dossiers/<trajectory-id>`.

## Query contracts

Versioned contracts cover:

* **reliability** - completion, partial reasons, retries, fallbacks, human
  intervention, and late-event counts
* **cost_latency** - models, providers, token use, latency, and cost by currency
* **policy** - observed allow, deny, review, and defer decisions
* **evaluation** - active labels, disagreement, supersession, and late evidence
* **harness_fit** - comparative completion, retry, fallback, latency, and cost by
  harness and model

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
