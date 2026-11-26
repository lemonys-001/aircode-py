from aircode.store import Store


def test_session_round_trip(tmp_path):
    store = Store(tmp_path / "sessions.db")
    try:
        sid = store.create_session(model="opencode/test-free", cwd=str(tmp_path))
        store.rename_from_first_message(sid, "  Fix   the parser  ")
        store.add_message(sid, "user", "hello")
        store.add_message(sid, "assistant", "hi", tool_calls=[{"id": "c1"}])
        store.add_message(sid, "tool", "output", tool_call_id="c1", name="read")

        session = store.get_session(sid)
        assert session["title"] == "Fix the parser"
        assert session["model"] == "opencode/test-free"
        assert store.latest_session()["id"] == sid
        assert store.list_sessions()[0]["message_count"] == 3

        messages = store.messages(sid)
        assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
        assert messages[1]["tool_calls"] == [{"id": "c1"}]
        assert store.export_session(sid)["session"]["id"] == sid

        assert store.delete_session(sid) is True
        assert store.get_session(sid) is None
        assert store.delete_session(sid) is False
    finally:
        store.close()


def test_title_is_not_overwritten(tmp_path):
    store = Store(tmp_path / "sessions.db")
    try:
        sid = store.create_session(model="m", cwd=str(tmp_path))
        store.rename_from_first_message(sid, "first")
        store.rename_from_first_message(sid, "second")
        assert store.get_session(sid)["title"] == "first"
    finally:
        store.close()
