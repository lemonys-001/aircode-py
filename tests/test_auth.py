import asyncio
import json

import httpx
import pytest

from aircode import auth as auth_module
from aircode.auth import AuthError, CredentialStore, ZenAuth


def test_device_login_and_logout(tmp_path, monkeypatch):
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/console/auth/device/code":
            return httpx.Response(
                200,
                json={
                    "device_code": "device",
                    "user_code": "ABCD-EFGH",
                    "verification_uri_complete": "/device",
                    "expires_in": 30,
                    "interval": 0,
                },
            )
        if path == "/console/auth/device/token":
            polls["count"] += 1
            if polls["count"] == 1:
                return httpx.Response(200, json={"error": "authorization_pending"})
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
        if path == "/console/api/orgs":
            assert request.headers["authorization"] == "Bearer access"
            return httpx.Response(200, json=[{"id": "org1", "name": "One"}])
        return httpx.Response(404)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(auth_module.asyncio, "sleep", no_sleep)
    store = CredentialStore(tmp_path / "auth.json")
    zen = ZenAuth(store, transport=httpx.MockTransport(handler))
    seen = []

    credential = asyncio.run(
        zen.device_login(on_code=seen.append, open_browser=False)
    )

    assert credential.access == "access"
    assert credential.org == "org1"
    assert seen[0].user_code == "ABCD-EFGH"
    assert seen[0].url.endswith("/device")
    assert zen.logged_in() is True
    assert store.get()["org"] == "org1"
    assert zen.logout() is True
    assert zen.logged_in() is False


def test_refresh_on_expired_credential(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/console/auth/device/token"
        payload = json.loads(request.content)
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "refresh"
        return httpx.Response(
            200,
            json={"access_token": "new", "refresh_token": "next", "expires_in": 3600},
        )

    store = CredentialStore(tmp_path / "auth.json")
    store.set(
        "opencode",
        {
            "type": "oauth",
            "access": "old",
            "refresh": "refresh",
            "expires": 1,
            "org": "org1",
        },
    )
    zen = ZenAuth(store, transport=httpx.MockTransport(handler))
    credential = asyncio.run(zen.access())
    assert credential.access == "new"
    assert credential.org == "org1"
    assert store.get()["access"] == "new"


def test_missing_credential_raises(tmp_path):
    zen = ZenAuth(CredentialStore(tmp_path / "auth.json"))
    with pytest.raises(AuthError):
        asyncio.run(zen.access())


def test_import_opencode_does_not_overwrite_by_default(tmp_path):
    source = tmp_path / "opencode.json"
    source.write_text(
        json.dumps(
            {
                "opencode": {"type": "oauth", "access": "imported"},
                "other": {"type": "api", "key": "k"},
            }
        )
    )
    store = CredentialStore(tmp_path / "auth.json")
    store.set("opencode", {"type": "oauth", "access": "existing"})
    zen = ZenAuth(store)

    assert zen.import_opencode(path=source) == ["other"]
    assert store.get("opencode")["access"] == "existing"
    assert store.get("other")["key"] == "k"
