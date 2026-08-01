---
name: ollama
description: Route Ollama model discovery, prompting, backend, context, or inference work through Agent Proxy in this environment. Use when a request mentions Ollama unless the task explicitly concerns Agent Proxy implementation, parity testing, or named incident isolation.
---

# Ollama through Agent Proxy

Treat Ollama as a physical backend behind Agent Proxy. Use the [Agent Proxy router](../repo-agent-proxy/SKILL.md) and its logical, governed surfaces instead of contacting Ollama directly.

Backend-direct access is limited to Agent Proxy implementation, parity testing, or explicitly named incident isolation. Name the exception when one applies.
