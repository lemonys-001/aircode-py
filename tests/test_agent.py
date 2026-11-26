import json

import pytest

from aircode.agent import run_agent
from aircode.config import Config
from aircode.store import Store


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def stream(self, model, messages, tools, *, session_id, request_id):
        self.calls.append([dict(message) for message in messages])
        if len(self.calls) == 1:
            yield {
                "type": "tool_call",
                "index": 0,
                "id": "call_1",
                "name": "write",
                "arguments": json.dumps({"path": "out.txt", "content": "ok"}),
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
            return
        yield {"type": "text", "text": "finished"}
        yield {"type": "done", "finish_reason": "stop"}


@pytest.mark.asyncio
async def test_agent_runs_tool_and_keeps_history_clean(tmp_path):
    store = Store(tmp_path / "sessions.db")
    provider = FakeProvider()
    events = []

    async def emit(event):
        events.append(event)

    try:
        sid = await run_agent(
            store,
            Config(cwd=tmp_path, auto_approve=True, max_steps=3),
            None,
            "write a file",
            emit=emit,
            provider=provider,
            model="opencode/test-free",
        )

        assert (tmp_path / "out.txt").read_text() == "ok"
        assert [message["role"] for message in store.messages(sid)] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert any(event["type"] == "tool_end" and not event["denied"] for event in events)
        assert events[-1]["type"] == "done"

        second = provider.calls[1]
        assistants = [message for message in second if message.get("role") == "assistant"]
        assert assistants
        assert all(message.get("tool_calls") for message in assistants)
        assert all(
            "tool_calls" not in message or message["tool_calls"] is not None
            for message in second
        )
        tool_messages = [message for message in second if message.get("role") == "tool"]
        assert tool_messages[0]["tool_call_id"] == "call_1"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_agent_denies_tool_when_permission_is_refused(tmp_path):
    store = Store(tmp_path / "sessions.db")
    provider = FakeProvider()
    asked = []

    async def deny(request):
        asked.append(request)
        return False

    async def emit(event):
        return None

    try:
        sid = await run_agent(
            store,
            Config(cwd=tmp_path, auto_approve=False, max_steps=3),
            None,
            "write a file",
            emit=emit,
            permission=deny,
            provider=provider,
            model="opencode/test-free",
        )

        assert not (tmp_path / "out.txt").exists()
        assert asked and asked[0].tool == "write"
        tool_messages = [m for m in store.messages(sid) if m["role"] == "tool"]
        assert "Permission denied" in tool_messages[0]["content"]
    finally:
        store.close()


def test_trim_keeps_tool_call_turns_whole():
    from aircode.agent import _trim

    messages = [
        {"role": "user", "content": "one"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "read"}}],
        },
        {"role": "tool", "content": "result", "tool_call_id": "c1"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "answer"},
    ]
    kept = _trim(messages, budget=0)
    assert kept == messages[3:]
    assert kept[0]["role"] == "user"


class FakeReadProvider:
    def __init__(self, path="note.txt"):
        self.path = path
        self.calls = 0

    async def stream(self, model, messages, tools, *, session_id, request_id):
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call",
                "index": 0,
                "id": "call_1",
                "name": "read",
                "arguments": json.dumps({"path": self.path}),
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
            return
        yield {"type": "text", "text": "read it"}
        yield {"type": "done", "finish_reason": "stop"}


@pytest.mark.asyncio
async def test_agent_reads_without_permission_prompt(tmp_path):
    (tmp_path / "note.txt").write_text("hello")
    store = Store(tmp_path / "sessions.db")

    async def emit(event):
        return None

    async def permission(request):
        raise AssertionError(f"unexpected prompt for {request.tool}")

    try:
        sid = await run_agent(
            store,
            Config(cwd=tmp_path, auto_approve=False, max_steps=3),
            None,
            "read note",
            emit=emit,
            permission=permission,
            provider=FakeReadProvider(),
            model="opencode/test-free",
        )
        tool_messages = [m for m in store.messages(sid) if m["role"] == "tool"]
        assert "hello" in tool_messages[0]["content"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_agent_asks_before_reading_outside_project(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    store = Store(tmp_path / "sessions.db")
    asked = []

    async def emit(event):
        return None

    async def permission(request):
        asked.append(request)
        return False

    try:
        sid = await run_agent(
            store,
            Config(cwd=tmp_path, auto_approve=False, max_steps=3),
            None,
            "read outside",
            emit=emit,
            permission=permission,
            provider=FakeReadProvider("../outside.txt"),
            model="opencode/test-free",
        )
        assert asked and asked[0].tool == "read"
        tool_messages = [m for m in store.messages(sid) if m["role"] == "tool"]
        assert "Permission denied" in tool_messages[0]["content"]
    finally:
        outside.unlink(missing_ok=True)
        store.close()


def test_outside_checks_glob_patterns(tmp_path):
    from aircode.agent import _outside

    assert _outside(tmp_path, {"pattern": "../*"}, "glob")
    assert not _outside(tmp_path, {"pattern": "**/*.py"}, "glob")
    assert not _outside(tmp_path, {"path": "src", "pattern": "*.py"}, "glob")
