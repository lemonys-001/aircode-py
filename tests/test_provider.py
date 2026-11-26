import json

import httpx
import pytest

from aircode.auth import AuthError
from aircode.catalog import Model
from aircode.provider import ProviderError, ZenProvider


def sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (body + "data: [DONE]\n\n").encode()


def model() -> Model:
    return Model(id="test-free", name="Test", base_url="https://example.test/v1")


@pytest.mark.asyncio
async def test_stream_parses_text_reasoning_tools_and_usage():
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"reasoning_content": "because"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "read", "arguments": '{"path"'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": ': "a.py"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(*chunks),
        )

    provider = ZenProvider("token", transport=httpx.MockTransport(handler))
    events = [
        event
        async for event in provider.stream(
            model(), [{"role": "user", "content": "hi"}], None,
            session_id="s", request_id="r",
        )
    ]

    assert {"type": "text", "text": "Hel"} in events
    assert {"type": "reasoning", "text": "because"} in events
    tools = [event for event in events if event["type"] == "tool_call"]
    assert tools[0]["name"] == "read"
    assert "".join(tool["arguments"] for tool in tools) == '{"path": "a.py"}'
    assert events[-1] == {"type": "done", "finish_reason": "tool_calls"}
    assert any(event["type"] == "usage" for event in events)


@pytest.mark.asyncio
async def test_json_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "list", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"total_tokens": 7},
            },
        )

    provider = ZenProvider("token", transport=httpx.MockTransport(handler))
    events = [
        event
        async for event in provider.stream(
            model(), [], None, session_id="s", request_id="r"
        )
    ]
    assert {"type": "text", "text": "hello"} in events
    assert any(event["type"] == "tool_call" for event in events)
    assert events[-1]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_retries_without_stream_options():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            assert json.loads(request.content)["stream_options"]
            return httpx.Response(
                400,
                json={"error": {"message": "stream_options is not supported"}},
            )
        assert "stream_options" not in json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse({"choices": [{"delta": {"content": "ok"}}]}),
        )

    provider = ZenProvider("token", transport=httpx.MockTransport(handler))
    events = [
        event
        async for event in provider.stream(
            model(), [], None, session_id="s", request_id="r"
        )
    ]
    assert calls["count"] == 2
    assert {"type": "text", "text": "ok"} in events


@pytest.mark.asyncio
async def test_auth_and_provider_errors():
    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad token"}})

    provider = ZenProvider("token", transport=httpx.MockTransport(unauthorized))
    with pytest.raises(AuthError):
        [
            event
            async for event in provider.stream(
                model(), [], None, session_id="s", request_id="r"
            )
        ]

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    provider = ZenProvider("token", transport=httpx.MockTransport(server_error))
    with pytest.raises(ProviderError) as error:
        [
            event
            async for event in provider.stream(
                model(), [], None, session_id="s", request_id="r"
            )
        ]
    assert error.value.status == 500
