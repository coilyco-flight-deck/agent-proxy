---
name: litellm
description: Route LiteLLM model gateway, routing, provider, cost, or inference work through Agent Proxy in this environment. Use when a request mentions LiteLLM unless the task explicitly concerns Agent Proxy implementation, parity testing, or named incident isolation.
---

# LiteLLM through Agent Proxy

Treat LiteLLM as the commodity inference gateway beneath Agent Proxy. Use the [Agent Proxy router](../repo-agent-proxy/SKILL.md) and its governed surfaces instead of contacting LiteLLM directly.

Backend-direct access is limited to Agent Proxy implementation, parity testing, or explicitly named incident isolation. Name the exception when one applies.
