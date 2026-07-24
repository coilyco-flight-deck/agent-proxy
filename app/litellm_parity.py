"""Executable LiteLLM boundary decision and endpoint parity probes."""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

ParityStatus = Literal["supported", "partial", "not_supported", "agent_proxy_retains"]


class CapabilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    standalone: ParityStatus
    sdk: ParityStatus
    selected_owner: str
    evidence: str
    cutover_gate: str


class ProbeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str


class EndpointProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    model: str
    checks: tuple[ProbeCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class ParityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_boundary: str
    baseline: EndpointProbe
    candidate: EndpointProbe
    matching_checks: tuple[ProbeCheck, ...]
    surface_parity_passed: bool
    cutover_authorized: bool = False


def capability_matrix() -> tuple[CapabilityDecision, ...]:
    """Decision evidence encoded for review and regression tests."""

    return (
        CapabilityDecision(
            capability="OpenAI-compatible chat and completion surface",
            standalone="supported",
            sdk="supported",
            selected_owner="LiteLLM standalone",
            evidence="LiteLLM documents OpenAI-format requests and responses in both modes.",
            cutover_gate="Chat, completion, tool-call, reasoning, and error fixtures pass.",
        ),
        CapabilityDecision(
            capability="Streaming",
            standalone="supported",
            sdk="supported",
            selected_owner="LiteLLM standalone",
            evidence="Both modes expose OpenAI-format streamed completion chunks.",
            cutover_gate="Chunk ordering, finish reasons, usage, and [DONE] pass against the tower.",
        ),
        CapabilityDecision(
            capability="Provider routing, retries, and fallbacks",
            standalone="supported",
            sdk="supported",
            selected_owner="LiteLLM standalone",
            evidence="The Proxy and SDK Router both document routing and failover.",
            cutover_gate="Configured retry and fallback paths pass deterministic failure fixtures.",
        ),
        CapabilityDecision(
            capability="Virtual keys, budgets, rate limits, and spend accounting",
            standalone="supported",
            sdk="partial",
            selected_owner="LiteLLM standalone",
            evidence="Multi-tenant keys and budget enforcement are Proxy gateway features.",
            cutover_gate="Key isolation, budget rejection, and spend attribution pass with Postgres.",
        ),
        CapabilityDecision(
            capability="Cost, latency, and token callbacks",
            standalone="supported",
            sdk="supported",
            selected_owner="LiteLLM standalone",
            evidence="Both modes expose callback data, while the Proxy also persists spend.",
            cutover_gate="Agent Proxy receives final cost, latency, token, retry, and fallback facts.",
        ),
        CapabilityDecision(
            capability="OpenTelemetry hooks",
            standalone="supported",
            sdk="supported",
            selected_owner="LiteLLM standalone",
            evidence="LiteLLM documents OpenTelemetry callbacks for gateway and SDK calls.",
            cutover_gate="Trace context joins LiteLLM, Agent Proxy, and Ward without body leakage.",
        ),
        CapabilityDecision(
            capability="Independent commodity-gateway lifecycle",
            standalone="supported",
            sdk="not_supported",
            selected_owner="LiteLLM standalone",
            evidence="A standalone process can upgrade, health-check, and roll back independently.",
            cutover_gate="Independent health, rollout, and rollback probes exist.",
        ),
        CapabilityDecision(
            capability="Live Ollama catalog and safe context derivation",
            standalone="partial",
            sdk="partial",
            selected_owner="Agent Proxy",
            evidence="LiteLLM model configuration does not replace Agent Proxy context verification.",
            cutover_gate="Real model discovery, num_ctx injection, and delivered-context checks pass.",
        ),
        CapabilityDecision(
            capability="Identity, policy, correlation, and structural detectors",
            standalone="not_supported",
            sdk="not_supported",
            selected_owner="Agent Proxy",
            evidence="These are domain boundary responsibilities, not commodity gateway behavior.",
            cutover_gate="Existing Agent Proxy fixtures stay green with LiteLLM underneath.",
        ),
    )


def selected_boundary() -> str:
    return "standalone"


async def probe_endpoint(
    endpoint: str,
    model: str,
    *,
    api_key: str = "parity-fixture",
    transport: httpx.AsyncBaseTransport | None = None,
) -> EndpointProbe:
    """Probe the stable surface without requiring provider-specific credentials."""

    checks: list[ProbeCheck] = []
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(
        base_url=endpoint.rstrip("/"),
        headers=headers,
        transport=transport,
        timeout=30,
    ) as client:
        try:
            response = await client.get("/v1/models")
            response.raise_for_status()
            payload = response.json()
            model_ids = {
                item.get("id") for item in payload.get("data", []) if isinstance(item, dict)
            }
            checks.append(
                ProbeCheck(
                    name="model_discovery",
                    passed=model in model_ids,
                    detail=f"discovered {len(model_ids)} models",
                )
            )
        except Exception as exc:
            checks.append(ProbeCheck(name="model_discovery", passed=False, detail=str(exc)))

        request = {
            "model": model,
            "messages": [{"role": "user", "content": "parity fixture"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "parity_tool",
                        "description": "Return fixture evidence.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "metadata": {
                "ward.run_id": "fixture-run",
                "agentproxy.request_id": "fixture-request",
            },
        }
        try:
            response = await client.post("/v1/chat/completions", json=request)
            response.raise_for_status()
            payload = response.json()
            choice = payload.get("choices", [{}])[0]
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            usage = payload.get("usage", {})
            shape_ok = (
                payload.get("object") == "chat.completion"
                and isinstance(message, dict)
                and isinstance(usage.get("total_tokens"), int)
            )
            checks.append(
                ProbeCheck(
                    name="chat_shape",
                    passed=shape_ok,
                    detail="OpenAI chat completion and numeric usage",
                )
            )
            checks.append(
                ProbeCheck(
                    name="finish_reason",
                    passed=isinstance(choice.get("finish_reason"), str),
                    detail=str(choice.get("finish_reason")),
                )
            )
        except Exception as exc:
            checks.append(ProbeCheck(name="chat_shape", passed=False, detail=str(exc)))
            checks.append(
                ProbeCheck(name="finish_reason", passed=False, detail="chat request failed")
            )

        try:
            response = await client.post(
                "/v1/completions",
                json={"model": model, "prompt": "parity fixture"},
            )
            response.raise_for_status()
            payload = response.json()
            choice = payload.get("choices", [{}])[0]
            usage = payload.get("usage", {})
            checks.append(
                ProbeCheck(
                    name="completion_shape",
                    passed=(
                        payload.get("object") == "text_completion"
                        and isinstance(choice.get("text"), str)
                        and isinstance(usage.get("total_tokens"), int)
                    ),
                    detail="OpenAI legacy completion and numeric usage",
                )
            )
        except Exception as exc:
            checks.append(ProbeCheck(name="completion_shape", passed=False, detail=str(exc)))

        stream_request = {**request, "stream": True}
        try:
            chunks: list[dict[str, Any]] = []
            done = False
            async with client.stream(
                "POST", "/v1/chat/completions", json=stream_request
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        done = True
                        continue
                    decoded = json.loads(data)
                    if isinstance(decoded, dict):
                        chunks.append(decoded)
            checks.append(
                ProbeCheck(
                    name="streaming",
                    passed=bool(chunks)
                    and done
                    and all(chunk.get("object") == "chat.completion.chunk" for chunk in chunks),
                    detail=f"{len(chunks)} chunks; done={done}",
                )
            )
        except Exception as exc:
            checks.append(ProbeCheck(name="streaming", passed=False, detail=str(exc)))

        try:
            response = await client.post(
                "/v1/chat/completions",
                json={**request, "model": "__agent_proxy_unknown_model__"},
            )
            checks.append(
                ProbeCheck(
                    name="unknown_model_error",
                    passed=400 <= response.status_code < 500,
                    detail=f"HTTP {response.status_code}",
                )
            )
        except Exception as exc:
            checks.append(ProbeCheck(name="unknown_model_error", passed=False, detail=str(exc)))

    return EndpointProbe(endpoint=endpoint, model=model, checks=tuple(checks))


async def compare_endpoints(
    baseline_url: str,
    candidate_url: str,
    model: str,
    *,
    baseline_api_key: str = "parity-fixture",
    candidate_api_key: str = "parity-fixture",
) -> ParityReport:
    baseline = await probe_endpoint(baseline_url, model, api_key=baseline_api_key)
    candidate = await probe_endpoint(candidate_url, model, api_key=candidate_api_key)
    baseline_by_name = {check.name: check for check in baseline.checks}
    candidate_by_name = {check.name: check for check in candidate.checks}
    matching = tuple(
        ProbeCheck(
            name=name,
            passed=baseline_by_name[name].passed and candidate_by_name[name].passed,
            detail=(
                f"baseline={baseline_by_name[name].detail}; "
                f"candidate={candidate_by_name[name].detail}"
            ),
        )
        for name in sorted(set(baseline_by_name) & set(candidate_by_name))
    )
    return ParityReport(
        selected_boundary=selected_boundary(),
        baseline=baseline,
        candidate=candidate,
        matching_checks=matching,
        surface_parity_passed=bool(matching) and all(check.passed for check in matching),
        cutover_authorized=False,
    )
