# Metrics-only route readiness

Agent Proxy separates process liveness, structural route readiness, and proof
of successful inference. Each signal answers a different operational question.

* **Service up** uses `GET /healthz`. It proves only that Agent Proxy can serve
  requests. It remains local so a downstream outage does not restart a healthy
  Agent Proxy process.
* **Route ready** uses `GET /readyz/{namespace}/{alias}`. It checks configuration,
  authentication, catalogs, and provider availability without model inference.
* **Inference recently verified** comes from passive production request metrics
  or a deliberately sparse canary. It is the only signal that proves model
  loading, context allocation, GPU execution, and a valid completion.

## Route readiness contract

For a LiteLLM-backed Ollama route, Agent Proxy checks these bounded control
surfaces concurrently:

* The strict logical route registry is loaded and the route is enabled.
* The route has Deploy-rendered physical readiness targets.
* Authenticated LiteLLM `GET /health/readiness` succeeds.
* Authenticated LiteLLM `GET /v1/models` contains the logical alias.
* Ollama `GET /api/version` succeeds.
* Ollama `GET /api/tags` contains every configured primary and fallback target.

For a LiteLLM-only hosted-provider route whose registry has no physical direct
or readiness target, Agent Proxy checks the route registry, LiteLLM
authentication, LiteLLM readiness, and the logical alias in the LiteLLM model
catalog. It skips Ollama checks because an unrelated local backend cannot prove
that the hosted route is available. The same route fails closed in direct mode.

Direct rollback mode skips LiteLLM and checks the configured Ollama backend.
It requires a supported physical direct target.
The readiness path never calls chat, completions, generation, or embeddings.
`PROXY_READINESS_TIMEOUT` bounds each dependency call and defaults to 3 seconds.

The endpoint returns `200` with `status: ready`, `503` with `status: not_ready`,
or `404` for an unknown or disabled route. A failure response lists only fixed
check names. It does not expose credentials, URLs, hosts, or physical models.

## Metrics and noise policy

`/healthz`, `/readyz/...`, and `/metrics` are metrics-only traffic. They do not
emit application logs, HTTP access logs, OpenTelemetry server or dependency
spans, trajectory events, Sentry events, Sentry breadcrumbs, or retained
bodies. Dependency failures remain visible through the endpoint response and
these bounded Prometheus series:

* `agent_proxy_health_endpoint_requests_total`
* `agent_proxy_readiness_checks_total`
* `agent_proxy_readiness_check_duration_seconds`
* `agent_proxy_route_ready`
* `agent_proxy_readiness_last_success_timestamp_seconds`

Check and outcome labels come from fixed code values. Logical-route labels come
only from the bounded mounted registry. Metrics never label raw URLs, hosts,
errors, request identifiers, credentials, or physical model names.

## Operational use

Kubernetes liveness must continue to use local process health. External uptime
monitoring may call route readiness for the exact Sirens Echo route it depends
on. Alerting should preserve three distinct states rather than collapsing them
into one binary health signal.
