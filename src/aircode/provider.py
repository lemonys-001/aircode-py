from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from . import __version__
from .auth import AuthError
from .catalog import Model


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class ZenProvider:
    """OpenAI-compatible streaming client for the OpenCode Zen gateway."""

    def __init__(
        self,
        access_token: str,
        *,
        org: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str | None = None,
    ):
        self.access_token = access_token
        self.org = org
        self.transport = transport
        self.user_agent = user_agent or f"aircode/{__version__}"

    def _headers(self, session_id: str, request_id: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": self.user_agent,
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-session": session_id,
            "x-opencode-session-id": session_id,
            "x-opencode-request": request_id,
        }
        if self.org:
            headers["x-opencode-org-id"] = self.org
        return headers

    def _payload(
        self,
        model: Model,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        include_usage: bool,
    ) -> dict:
        payload: dict = {
            "model": model.id,
            "messages": messages,
            "stream": True,
        }
        if include_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def stream(
        self,
        model: Model,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        session_id: str,
        request_id: str,
    ) -> AsyncIterator[dict]:
        if model.api != "chat-completions":
            raise ProviderError(
                f"{model.id} uses the {model.api} protocol, which this build does not speak yet."
            )
        url = model.base_url.rstrip("/") + "/chat/completions"
        include_usage = True
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0),
            transport=self.transport,
        ) as client:
            for _attempt in range(2):
                request = client.build_request(
                    "POST",
                    url,
                    headers=self._headers(session_id, request_id),
                    json=self._payload(model, messages, tools, include_usage=include_usage),
                )
                response = await client.send(request, stream=True)
                if response.status_code == 400 and include_usage:
                    body = (await response.aread()).decode("utf-8", "replace")
                    if "stream_options" in body or "include_usage" in body:
                        include_usage = False
                        await response.aclose()
                        continue
                    await response.aclose()
                    raise self._error(response.status_code, body)
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    await response.aclose()
                    raise self._error(response.status_code, body)
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    body = (await response.aread()).decode("utf-8", "replace")
                    await response.aclose()
                    async for event in self._parse_json_response(body):
                        yield event
                    return
                finish_reason = ""
                try:
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("error"):
                            error = chunk["error"]
                            message = error.get("message") if isinstance(error, dict) else str(error)
                            raise ProviderError(str(message or "The provider returned an error."))
                        usage = chunk.get("usage") if isinstance(chunk, dict) else None
                        if isinstance(usage, dict):
                            yield {"type": "usage", "usage": usage}
                        for choice in chunk.get("choices", []) if isinstance(chunk, dict) else []:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") or {}
                            text = _text_from_content(delta.get("content"))
                            if text:
                                yield {"type": "text", "text": text}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            reasoning_text = _text_from_content(reasoning)
                            if reasoning_text:
                                yield {"type": "reasoning", "text": reasoning_text}
                            for tool_call in delta.get("tool_calls") or []:
                                if not isinstance(tool_call, dict):
                                    continue
                                function = tool_call.get("function") or {}
                                yield {
                                    "type": "tool_call",
                                    "index": int(tool_call.get("index") or 0),
                                    "id": tool_call.get("id"),
                                    "name": function.get("name"),
                                    "arguments": function.get("arguments") or "",
                                }
                            if choice.get("finish_reason"):
                                finish_reason = str(choice["finish_reason"])
                finally:
                    await response.aclose()
                yield {"type": "done", "finish_reason": finish_reason or "stop"}
                return
        raise ProviderError("The request did not produce a response.")

    def _error(self, status: int, body: str) -> Exception:
        message = ""
        try:
            payload = json.loads(body)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("type") or "")
            elif error:
                message = str(error)
            message = message or str(payload.get("message") or "")
        except (ValueError, AttributeError):
            pass
        lowered = body.lower()
        if "freetiererror" in lowered or "free tier" in lowered:
            message = (
                "OpenCode's free tier rejected this client. The tier is gated to the "
                "official client; try an API-key provider instead."
            )
        if status in (401, 403) and "free tier" not in lowered:
            return AuthError(message or f"Authentication failed (HTTP {status}).")
        return ProviderError(
            message or f"Provider request failed (HTTP {status}).",
            status=status,
            body=body[:2000],
        )

    async def _parse_json_response(self, body: str) -> AsyncIterator[dict]:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise ProviderError(f"Unexpected response from the provider: {body[:300]}") from None
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ProviderError(str(message or "The provider returned an error."))
        choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
        message = choice.get("message") or {}
        text = _text_from_content(message.get("content"))
        if text:
            yield {"type": "text", "text": text}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        reasoning_text = _text_from_content(reasoning)
        if reasoning_text:
            yield {"type": "reasoning", "text": reasoning_text}
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            yield {
                "type": "tool_call",
                "index": index,
                "id": tool_call.get("id"),
                "name": function.get("name"),
                "arguments": function.get("arguments") or "",
            }
        if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
            yield {"type": "usage", "usage": payload["usage"]}
        yield {"type": "done", "finish_reason": str(choice.get("finish_reason") or "stop")}
