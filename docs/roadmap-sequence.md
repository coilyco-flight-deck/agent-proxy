# Baseline and implementation sequence

Part of [ROADMAP](ROADMAP.md).

## Current baseline


- The current service is an OpenAI-compatible reliability gateway with logical
  route validation, context protection, queueing, validation, retries,
  fallbacks, circuit breaking, and operational telemetry.
- The request path remains stateless except for its in-memory queue and caches.
- Ward correlation metadata reaches logs and traces.
- Skill-use artifact ingestion durably retains contract-v1 observations and
  keeps logs and Prometheus as operational projections.
- Append-only SQLite retention, replay, materialization, evaluation joins,
  dataset exports, and governed views have landed in the cold path.
- SigNoz and OTLP support operations without becoming the trajectory system of
  record.

## Implementation sequence


1. [#41 LiteLLM standalone versus SDK parity spike](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/41)
   - Landed: selects the standalone LiteLLM Proxy and adds an executable surface parity runner.
   - Current reliability behavior stays until the documented live tower, key, budget, spend, context-safety, and trace gates pass.
2. [#42 Trajectory schema package and validation fixtures](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/42)
   - Landed: establishes the shared executable contract independently of the retention system.
3. [#43 Append-only ingestion and replayable raw retention](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/43)
   - Depends on #42.
   - Landed: SQLite WAL retention, immutable receipt and quarantine ledgers, replay, and a bounded asynchronous emitter provide durable evidence outside the hot path.
4. [#44 Episode and trajectory materialization](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/44)
   - Depends on #42 and #43.
   - Landed: deterministically joins retained events, exposes partial and late state, and appends provenance-preserving derived revisions.
5. [#45 Evaluation and annotation records](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/45)
   - Depends on #42 and #44.
   - Landed: evaluator, verifier, annotation, and human-intervention records append immutable evidence without changing Ward authority.
6. [#46 Versioned training and held-out evaluation exports](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/46)
   - Depends on #44 and #45.
   - Landed: reproducible SFT, preference, verifier, reward, and held-out evaluation artifacts use write-once provenance manifests and trajectory-level splits.
7. [#47 Operational queries, Ward dossier inputs, and harness-fit views](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/47)
   - Depends on #43, #44, and #45.
   - Landed: exposes governed operational evidence, OTLP joins, harness-fit comparisons, and evidence-only dossier inputs without moving authority out of Ward.
