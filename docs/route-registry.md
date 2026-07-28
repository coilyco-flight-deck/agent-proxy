# Logical route registry

Agent Proxy accepts stable `<role>/<intent>` keys from governed clients. Deploy
mounts a service-local JSON registry that resolves each key without exposing a
physical backend through `/v1/models`, MCP discovery, responses, or public
telemetry.

## Ownership

* AOSH scores the primary and fallback backend choices.
* Deploy renders AOSH's versioned source into this service-specific schema.
* Agent Proxy validates keys, applies context policy, and dispatches an alias.
* LiteLLM translates the alias into provider routing, retry, and fallback.

Agent Proxy never imports or fetches AOSH. Endpoints, secret paths, and
resources stay in Deploy-owned backend configuration, outside this registry.

## Version 1

```json
{
  "format": "agent-proxy-route-registry/v1",
  "source": {
    "format": "aosh.agent-proxy-routes",
    "version": 1,
    "revision": "source-revision",
    "sha256": "source-digest"
  },
  "routes": [
    {
      "key": "community/knowledge-retrieval",
      "upstream_alias": "community/knowledge-retrieval",
      "direct": {
        "model": "ornith:35b",
        "runtime": "ollama"
      }
    }
  ]
}
```

`upstream_alias` is sent to LiteLLM. `direct` is optional deployment data for
the established rollback path. Direct mode currently supports Ollama targets.
A known llama.cpp target fails closed instead of silently selecting another
model.

The loader rejects unknown formats or fields, duplicate keys, missing aliases,
malformed targets, unsafe file types, oversized files, and invalid JSON.
Configured invalid files stop process startup before traffic is served.

## Configuration

* `PROXY_ROUTE_REGISTRY_FILE` points to the mounted JSON file.
* `PROXY_ROUTE_UPSTREAM_MODE=litellm` sends `upstream_alias` to the configured
  OpenAI-shaped inner gateway.
* `PROXY_ROUTE_UPSTREAM_MODE=direct` sends a supported physical direct target.
* `PROXY_ROUTE_REGISTRY_COMPATIBILITY_MODE=true` permits legacy physical-model
  discovery only when no registry path is set. Production sets it to `false`.

Logical key and upstream mode are trace, log, and metric dimensions. Physical
identity remains restricted backend telemetry. Neither logical route nor role
is appended to messages or prompts. Body-safe LiteLLM metadata remains outside
model-visible context.
