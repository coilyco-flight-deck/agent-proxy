# Architecture v2 work graph

The issues below are fresh implementation work for Agent Proxy architecture v2. They are intentionally separate from closed reliability-proxy issues, which remain evidence only.

1. [#41 LiteLLM standalone versus SDK parity spike](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/41)
   - No dependency.
   - Proves the selected commodity gateway integration before current behavior is removed.
2. [#42 Trajectory schema package and validation fixtures](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/42)
   - No dependency.
   - Implements the shared contract in [`trajectory-contract-v1.md`](trajectory-contract-v1.md).
3. [#43 Append-only ingestion and replayable raw retention](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/43)
   - Depends on #42.
   - Landed: establishes durable, replayable SQLite evidence without using SigNoz as the sole store.
4. [#44 Episode and trajectory materialization](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/44)
   - Depends on #42 and #43.
   - Builds deterministic, re-materializable correlated trajectories.
5. [#45 Evaluation and annotation records](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/45)
   - Depends on #42 and #44.
   - Adds evaluator, verifier, annotation, and human-intervention evidence.
6. [#46 Versioned training and held-out evaluation exports](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/46)
   - Depends on #44 and #45.
   - Produces reproducible SFT, preference, verifier, reward, and held-out evaluation manifests.
7. [#47 Operational queries, Ward dossier inputs, and harness-fit views](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/47)
   - Depends on #43, #44, and #45.
   - Produces governed evidence views while keeping Ward as the authority.

Each issue contains its own explicit acceptance criteria. #41 and #42 can proceed in parallel. #43 follows the schema package, and the remaining data-plane work follows the listed dependencies.
