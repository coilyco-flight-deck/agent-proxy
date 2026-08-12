# Landed gateway and ingestion capabilities

Part of [FEATURES.md](FEATURES.md).

- **LiteLLM parity decision and runner** - landed - a machine-readable comparison selects standalone LiteLLM, while an executable endpoint probe gates model discovery, chat shape, streaming, finish reasons, usage, and error mapping.
- **Authenticated LiteLLM inner-gateway client** - landed - mounted-file bearer authentication, service-key model filtering, tower-backed context metadata, safe top-level `num_ctx`, OpenAI option translation, and body-safe Ward correlation support a standalone LiteLLM hop without weakening Agent Proxy policy or trajectory ownership.
- **Provider prompt-cache accounting** - landed - the proxy normalizes DeepSeek,
  OpenAI-compatible, and Anthropic-style cache usage into the response usage
  block, spans, Prometheus counters, and the trajectory ledger, and keeps a
  provider that reports nothing distinguishable from a measured cache miss. It
  does not inject cache breakpoints or own a caching policy. See
  [proxy-prompt-cache.md](proxy-prompt-cache.md).
- **Agent-compose trajectory ingestion** - landed - a cold-path adapter maps the
  immutable manifest and public-safe decision trace into actor, artifact, and
  observation events without copying the opaque context tree or granting
  execution authority. See [agent-compose-ingestion.md](agent-compose-ingestion.md).
- **Guard trajectory ingestion** - landed - cold-path adapters map cli-guard
  audit rows into action, policy, and execution events, and hash specgen
  guardfiles and locks into linked policy evidence without retaining sensitive
  argv, diagnostics, paths, or hosts. See [guard-ingestion.md](guard-ingestion.md).
- **Runtime and delivery checks** - landed - SSM-backed configuration, local
  `/healthz`, metrics-only non-generating route readiness, `/metrics`,
  daemonless boot probing, container probing, and a reliability harness. Route
  readiness verifies authenticated LiteLLM control surfaces for hosted routes
  and adds Ollama catalog checks only when the registry declares local physical
  targets. It does not claim that GPU execution or completion validity was
  proven. See [readiness.md](readiness.md).
