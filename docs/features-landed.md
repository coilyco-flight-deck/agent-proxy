# Landed capabilities

Part of [FEATURES](FEATURES.md).

## Landed reliability collection tap


- **OpenAI-compatible request surface** - landed - `/v1/chat/completions`, `/v1/completions`, and `/v1/models`, including streaming and normalized reasoning content.
- **Remote MCP prompt surface** - landed - stateless Streamable HTTP at `/mcp`
  exposes model discovery and non-streaming prompt tools through the existing
  Agent Proxy policy, reliability, telemetry, and trajectory path. See
  [mcp.md](mcp.md).
- **Logical route registry** - landed - strict Deploy-mounted service and
  evaluation aliases hide physical backends from governed clients, route
  aliases through LiteLLM, and fail closed when direct rollback cannot serve a runtime. See
  [route-registry.md](route-registry.md).
- **Backend-derived context safety** - landed - safe `num_ctx` derivation and injection, `OLLAMA_NUM_PARALLEL` compensation, context-budget trimming, and loud delivered-context truncation detection.
- **Current gateway resilience** - landed - bounded in-memory queue and workers, queue backpressure, structural response validation, retry with backoff, fallback chains, and per-backend circuit breakers. Validation rejects only structurally broken output (empty, unparsable tool arguments, truncation garbage, degenerate repetition); it does not judge the meaning of assistant text.
- **Operational evidence** - landed - trace-correlated structured JSON logs, Prometheus metrics, OpenTelemetry traces, closed-set SigNoz exception events for every handled runtime failure under a bounded 13-code taxonomy with stage tags, Sentry initialization, request spans, and Ollama final-response token plus phase-duration measurements for streaming and non-streaming requests.
- **Opt-in complete model I/O capture** - landed - complete normalized request
  and response bodies for non-streaming chat, reconstructed streaming chat,
  text completions, and MCP prompt calls are written to paired structured events
  and request-span attributes. Capture defaults off, fails hard on field loss,
  and is enabled on ser8 against restricted SigNoz storage. See
  [issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).
- **Ward correlation** - landed - request, Ward run, workflow, repository, issue, and agent-session metadata joins in logs and spans.
- **Skill-use artifact observation** - landed - Ward reap `skill-usage.json`
  parsing durably retains metadata-only skill observations with run and
  engineering correlations while preserving structured logs and the
  `ward_skill_use_total` Prometheus counter.

The trajectory, evaluation, and dataset capabilities continue in
[features-landed-trajectory.md](features-landed-trajectory.md).
