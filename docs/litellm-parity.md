# LiteLLM parity decision

The standalone-versus-SDK LiteLLM decision, the capability comparison behind it,
and the gates that must pass before any Agent Proxy responsibility retires.

Agent Proxy selects a **standalone LiteLLM Proxy** as its inner commodity
gateway. The Python SDK is not selected.

This decision does not delete current reliability behavior. The authenticated
inner-gateway client is implemented, while deployment activation and joined
live evidence remain separate gates.

## Contents

- [Decision and capability result](litellm-parity-decision.md)
- [Gates, boundary, and blockers](litellm-parity-gates.md)
