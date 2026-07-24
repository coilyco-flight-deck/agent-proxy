# Evaluation and annotation records

Evaluation evidence is an append-only projection joined to a materialized
trajectory and its raw source event. Agent Proxy records evidence. Ward remains
the authorization and execution authority.

## Record kinds

`agentproxy.trajectory.evaluation` version `1.0` covers:

* automatic evaluations
* verifier outputs
* human annotations
* human interventions

Every record names the evaluator implementation, rubric or verifier version,
input and evidence references, label or score, confidence, origin, and source
event. Human records also preserve an opaque annotator reference and role.
Large rationale or annotation bodies remain external content references.

## Supersession and disagreement

Corrections append a new record with `supersedes_evaluation_id`. The prior
record remains immutable evidence. Derived summaries exclude superseded records
from their active set while retaining their ids.

Multiple active labels remain explicit disagreement. No exporter may select a
winner silently. A later resolving annotation must state which record it
supersedes and causes a new derived dataset version.

## Late annotations

An evaluation observed after the trajectory revision's materialization time is
marked late. The raw event causes trajectory re-materialization, then evaluation
assembly joins against the new stable trajectory revision. Any dataset or view
that consumed the older revision must publish a new version or declare its
immutable cutoff.

## Privacy and access

Evaluation records preserve capture mode, redaction status, redaction policy,
and access tier from their source envelope. A restricted annotation makes the
evaluation summary restricted. Derived consumers may narrow access but cannot
upgrade it.

`EvaluationStore` appends content-hashed records to SQLite. Reusing an
evaluation id with different evidence is rejected. Database triggers reject
updates and deletes. The raw ledger remains the replay source, and repository
fixtures prove automatic, human, intervention, supersession, disagreement,
late, restricted, and replay behavior.
