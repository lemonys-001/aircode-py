import re

from fastapi.testclient import TestClient

from aircode import server as server_module
from aircode.catalog import Model
from aircode.config import Config
from aircode.server import create_app
from aircode.store import Store


class FakeAuth:
    def logged_in(self):
        return True


def app_for(tmp_path, *, token=None, auth=None):
    store = Store(tmp_path / "sessions.db")
    config = Config(cwd=tmp_path, model="opencode/test-free")
    app = create_app(config, store=store, auth=auth or FakeAuth(), token=token)
    return app, store


def test_status_sessions_and_static(tmp_path):
    app, store = app_for(tmp_path)
    try:
        with TestClient(app) as client:
            status = client.get("/api/status")
            assert status.status_code == 200
            assert status.json()["logged_in"] is True
            assert status.json()["token_required"] is False

            created = client.post("/api/sessions", json={"model": "opencode/test-free"})
            assert created.status_code == 200
            session_id = created.json()["id"]
            assert client.get(f"/api/sessions/{session_id}").json()["messages"] == []
            assert client.get("/api/sessions").json()[0]["id"] == session_id
            assert client.delete(f"/api/sessions/{session_id}").json() == {"ok": True}
            assert client.get(f"/api/sessions/{session_id}").status_code == 404

            root = client.get("/")
            assert root.status_code == 200
            assert "Aircode" in root.text
            asset = re.search(r'src="\./assets/([^"]+\.js)"', root.text)
            assert asset
            assert client.get(f"/assets/{asset.group(1)}").status_code == 200
    finally:
        store.close()


def test_models_endpoint(tmp_path, monkeypatch):
    async def fake_models(*, force=False):
        return [Model(id="test-free", name="Test", free=True)]

    monkeypatch.setattr(server_module, "fetch_models", fake_models)
    app, store = app_for(tmp_path)
    try:
        with TestClient(app) as client:
            models = client.get("/api/models").json()
            assert models[0]["id"] == "test-free"
            assert models[0]["free"] is True
    finally:
        store.close()


def test_chat_streams_events(tmp_path, monkeypatch):
    async def fake_run_agent(store, config, session_id, text, *, emit, **kwargs):
        assert text == "hello"
        await emit(
            {
                "type": "session",
                "id": "ses_test",
                "title": "Hello",
                "model": "opencode/test-free",
            }
        )
        await emit({"type": "text", "text": "world"})
        await emit({"type": "done", "usage": {"total_tokens": 2}})
        return "ses_test"

    monkeypatch.setattr(server_module, "run_agent", fake_run_agent)
    app, store = app_for(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/api/chat", json={"text": "hello"})
            assert response.status_code == 200
            assert '"type": "session"' in response.text
            assert '"text": "world"' in response.text
            assert '"type": "done"' in response.text
            assert client.post("/api/chat", json={"text": ""}).status_code == 400
    finally:
        store.close()


def test_token_guard(tmp_path):
    app, store = app_for(tmp_path, token="secret")
    try:
        with TestClient(app) as client:
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status?token=secret").status_code == 200
            assert (
                client.get(
                    "/api/status", headers={"Authorization": "Bearer secret"}
                ).status_code
                == 200
            )
    finally:
        store.close()
