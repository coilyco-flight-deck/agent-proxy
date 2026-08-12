# Logical route registry

The Deploy-mounted logical route contract that keeps physical backend names
behind the proxy boundary, and how a deployment configures and rolls it back.

Agent Proxy accepts stable `<namespace>/<alias>` keys from governed clients.
Deploy mounts a service-local JSON registry that resolves each key without
exposing a physical backend through `/v1/models`, MCP discovery, responses, or
public telemetry. Namespaces identify owning services or evaluation surfaces,
not Agent Compose roles.

## Contents

- [Ownership and version 1 contract](route-registry-contract.md)
- [Configuration](route-registry-configuration.md)
