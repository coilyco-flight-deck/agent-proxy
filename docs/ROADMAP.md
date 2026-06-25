# Roadmap

The phased platform vision for `agent-proxy`. The repo identity is a capability platform for the local agent and LLM fleet. **Phase 1 is the reliability proxy**, the first deliverable and the only phase with a locked, build-ready spec today. The capability phases below are real, named, future work. They are documented here durably so the vision is not lost, and none is implemented until its phase opens.

The sequencing rule: phase 1 stays tightly scoped to the reliability proxy. The capability phases do not bleed into it.

## Phase 1 - reliability proxy

Drag every harness up to goose-level reliability by killing silent context truncation and capricious output behind one OpenAI-compatible API. The scope is locked in aosh leg `02-reference-architecture.md` and built per leg `04-headless-proxy-build.md`. Features are inventoried in `docs/FEATURES.md`.

Phase 1 maps to the aosh mission milestones M1 through M8 (`docs/plan/90-sequencing-and-milestones.md`). M0 (plan committed) is done in aosh.

* **M1 - truncation killed** - benchmark the tower, build the proxy core, inject per-model `num_ctx`, prove the 32k cliff is gone, and repoint one local opencode. The fastest, model-independent reliability win. (aosh headless legs 03, 04)
* **M2 - resilience core measured** - queue, validation, retry, fallback, and breaker, all instrumented, with the reliability harness giving the baseline-to-after number. Kai sets the SLO target at the consult checkpoint. (aosh headless legs 04, 05)
* **M3 - on kai-server** - the 2-pod decision, authorization, and the 2-replica proxy plus Caddy deployed with secrets from SSM and the tower hardened. (aosh consult leg 09)
* **M4 - harnesses repointed** - opencode, crush, openclaw, and openwebui moved onto the gateway with one native goose kept as control, each passing the reliability harness. (aosh consult leg 10)
* **M5 - matrix benchmarked, locked, pruned** - the five model families benchmarked, Kai locks the tags, extras pruned, siblings pulled. (aosh headless legs 03, 06 plus consult 08)
* **M6 - corrections landed** - diagnosis docs superseded, the FQDN leak fixed, models.json cleaned, and hardware facts reconciled with Kai. (aosh headless leg 07 plus consult 11)
* **M7 - wildcard fine-tune** - Kai names a dataset, the Unsloth loop runs, and the result serves as `tune` only if it beats base. (aosh consult leg 12)
* **M8 - rollout and docs** - Ansible roles in infrastructure deploy the gateway and pin tower ollama, and the README / AGENTS / FEATURES trifecta plus skills stay current. (aosh headless plus consult)

The critical path is M1 to M4, which deliver the reliability the mission is really about. M5 parallelizes with M3 to M4 because the proxy works with current models while families are benchmarked.

## Capability phases (future, not phase 1)

Each phase below captures a brainstorm item with a one-line intent and any known dependency. Every one is marked **future / not phase 1** and is not implemented until its phase is opened.

### Model upskilling - future, not phase 1

Improve weak model behavior, particularly tool use. This is where the proxy makes a mediocre local model behave like a better one. Depends on the aosh `tune` wildcard fine-tune path (leg 12) and the serving and eval paths existing, so it sequences late.

### Tool injection - future, not phase 1

Inject tools like web search and API calls into harness requests, so a harness gains capabilities it did not ship with. Depends on the OpenAI-compatible tool-call surface from phase 1 being solid.

### Credential injection - future, not phase 1

Hand harnesses scoped credentials without the harness ever holding them, particularly via MCP pass-through. Depends on tool injection and on the SSM secret path from phase 1.

### Knowledge management / RAG - future, not phase 1

Retrieval-augmented knowledge for requests, so the fleet can answer from a managed corpus. Depends on a persistence layer (see data persistence below).

### Data formatting / data management / persistence - future, not phase 1

Conventional capability enhancement with durable state. Phase 1 is deliberately stateless except the in-memory queue, so this phase introduces the first durable store and the formatting and management around it.

### Single-shot to multi-turn - future, not phase 1

Turn a single-shot capability into a multi-turn one, letting the proxy orchestrate a sequence on the harness's behalf. Depends on the resilience core and on persistence.

### i/o validation + formatting - future, not phase 1

Validate and shape request and response i/o. This overlaps the phase-1 resilience validation and extends it from "is this output usable" toward "is this output the right shape for the caller".

## Source of truth

The design is locked in aosh and is not duplicated here. See `docs/plan/02-reference-architecture.md`, `docs/plan/04-headless-proxy-build.md`, and `docs/plan/90-sequencing-and-milestones.md` in `coilyco-bridge/agentic-os-hardware`, and the tracking issue `coilysiren/inbox#118` whose two locked-design comments supersede parts of the original brief.
