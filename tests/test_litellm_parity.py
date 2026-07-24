"""LiteLLM boundary decision and executable endpoint probes."""

from __future__ import annotations

import json

import httpx

from app.litellm_parity import capability_matrix, probe_endpoint, selected_boundary


def _transport(*, stream_done: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "fixture-model", "object": "model"}],
                },
            )
        if request.url.path == "/v1/completions":
            return httpx.Response(
                200,
                json={
                    "id": "fixture-completion",
                    "object": "text_completion",
                    "model": "fixture-model",
                    "choices": [{"index": 0, "text": "ok", "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            )
        body = json.loads(request.content)
        if body.get("model") == "__agent_proxy_unknown_model__":
            return httpx.Response(404, json={"error": {"type": "model_not_found"}})
        if body.get("stream"):
            terminal = "data: [DONE]\n\n" if stream_done else ""
            content = (
                'data: {"object":"chat.completion.chunk","choices":'
                '[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
                'data: {"object":"chat.completion.chunk","choices":'
                '[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n' + terminal
            )
            return httpx.Response(
                200,
                text=content,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_standalone_is_selected_for_the_independent_gateway_boundary():
    assert selected_boundary() == "standalone"
    matrix = {row.capability: row for row in capability_matrix()}

    assert (
        matrix["Virtual keys, budgets, rate limits, and spend accounting"].standalone == "supported"
    )
    assert matrix["Independent commodity-gateway lifecycle"].sdk == "not_supported"
    assert matrix["Live Ollama catalog and safe context derivation"].selected_owner == "Agent Proxy"


async def test_endpoint_probe_covers_models_chat_stream_and_errors():
    probe = await probe_endpoint(
        "http://fixture",
        "fixture-model",
        transport=_transport(),
    )

    assert probe.passed
    assert [check.name for check in probe.checks] == [
        "model_discovery",
        "chat_shape",
        "finish_reason",
        "completion_shape",
        "streaming",
        "unknown_model_error",
    ]


async def test_missing_stream_terminal_fails_cutover_probe():
    probe = await probe_endpoint(
        "http://fixture",
        "fixture-model",
        transport=_transport(stream_done=False),
    )

    checks = {check.name: check for check in probe.checks}
    assert checks["streaming"].passed is False
