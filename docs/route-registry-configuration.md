# Configuration

Part of [route-registry](route-registry.md).

## Configuration


* `PROXY_ROUTE_REGISTRY_FILE` points to the mounted JSON file.
* `PROXY_ROUTE_UPSTREAM_MODE=litellm` sends `upstream_alias` to the configured
  OpenAI-shaped inner gateway.
* `PROXY_ROUTE_UPSTREAM_MODE=direct` sends a supported physical direct target.
* `PROXY_ROUTE_REGISTRY_COMPATIBILITY_MODE=true` permits legacy physical-model
  discovery only when no registry path is set. Production sets it to `false`.

Logical key and upstream mode are trace, log, and metric dimensions. Physical
identity remains restricted backend telemetry. Logical routes are not appended
to messages or prompts. Body-safe LiteLLM metadata remains outside
model-visible context.
