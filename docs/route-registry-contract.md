# Ownership and version 1 contract

Part of [route-registry](route-registry.md).

## Ownership


* Deploy owns service and evaluation aliases plus their selected backends.
* Agent Proxy validates keys, applies context policy, and dispatches an alias.
* LiteLLM translates the alias into provider routing, retry, and fallback.

Agent Proxy never imports or fetches a route source. Endpoints, secret paths,
and resources stay in Deploy-owned backend configuration, outside this
registry.

## Version 1


```json
{
  "format": "agent-proxy-route-registry/v1",
  "source": {
    "evaluation_routes_sha256": "evaluation-input-digest",
    "format": "deploy.agent-proxy-routes/v1",
    "service_routes_sha256": "service-input-digest",
    "version": 1
  },
  "routes": [
    {
      "key": "sirens-echo/default",
      "upstream_alias": "sirens-echo/default",
      "direct": {
        "model": "ornith:35b",
        "runtime": "ollama"
      },
      "readiness_targets": [
        {
          "model": "ornith:35b",
          "runtime": "ollama"
        },
        {
          "model": "ornith:9b",
          "runtime": "ollama"
        }
      ]
    }
  ]
}
```

The source object records the exact Deploy-owned service and evaluation inputs
used to generate the mounted registry. Agent Proxy retains both SHA-256 digests
as provenance while continuing to reject unrecognized source fields.

`upstream_alias` is sent to LiteLLM. `direct` is optional deployment data for
the established rollback path. Direct mode currently supports Ollama targets.
A known llama.cpp target fails closed instead of silently selecting another
model.

`readiness_targets` is an optional ordered list of physical primary and
fallback targets rendered by Deploy from the same route inputs that render
LiteLLM routing. Agent Proxy uses the list only for non-generating installed-model
checks. If it is absent, readiness checks the existing `direct` target for
backward compatibility. When both fields are absent, LiteLLM mode treats the
route as hosted-provider-only and verifies only the authenticated LiteLLM
control surfaces. Direct mode fails closed. Physical target names never enter
readiness responses, logs, traces, or metric labels.

The loader rejects unknown formats or fields, duplicate keys, missing aliases,
malformed targets, unsafe file types, oversized files, and invalid JSON.
Configured invalid files stop process startup before traffic is served.
