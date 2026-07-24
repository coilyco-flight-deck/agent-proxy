# Versioned dataset exports

Agent Proxy materializes five refs-first dataset kinds from stable trajectories
and active evaluation evidence:

* `agentproxy.dataset.sft`
* `agentproxy.dataset.preference`
* `agentproxy.dataset.verifier`
* `agentproxy.dataset.reward`
* `agentproxy.dataset.held_out_evaluation`

Each schema is version `1.0`. An export contains `manifest.json` plus canonical
JSONL examples.

## Immutable manifest

Every manifest records:

* schema and transform versions
* selection, split, and redaction policies
* source event, trajectory, and evaluation ids
* per-example content hashes
* access tier
* an immutable materialization-time query boundary
* a content-derived dataset id and manifest hash

`DatasetArtifactStore` is write once. Rewriting the same dataset id with
different bytes fails. Rebuilding from the same retained raw events,
materializer version, and policies produces the same id and bytes.

## Split semantics

`trajectory-hash-v1` assigns the complete trajectory to `train` or `held_out`
from a versioned seed and bucket policy. SFT, preference, verifier, and reward
exports select only training trajectories. Held-out evaluation exports select
only held-out trajectories.

No event, annotation, retry, or later revision from one trajectory can cross
the split. Changing the seed, bucket policy, source evidence, or materializer
creates a different manifest and dataset id.

## Selection

The default `active-evaluations-v1` policy excludes superseded evaluations and
keeps unresolved disagreement visible. Export-specific requirements then apply:

* SFT requires an active target label or score.
* Preference requires explicit chosen and rejected references.
* Verifier requires verifier evidence with an expected label or score.
* Reward requires an explicit reward or score.
* Held-out evaluation requires an expected label or score.

Empty exports remain valid versioned artifacts. The manifest makes the absence
of qualifying evidence auditable.

## Privacy

Examples contain stable references rather than copied prompt, response, tool,
or environment bodies. Restricted body references appear only when
`include_restricted_body_refs` is explicitly enabled. The export remains
restricted even when its default policy omits the body reference, because a
derived consumer cannot upgrade the source access tier.
