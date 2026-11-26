from __future__ import annotations

import asyncio
import json
import os
import time
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .paths import auth_path, import_opencode_auth_path

CONSOLE = "https://opencode.ai/console"
CLIENT_ID = "opencode-cli"
CONSOLE_USER_AGENT = "opencode/cli"
PROVIDER = "opencode"


class AuthError(RuntimeError):
    pass


@dataclass
class Org:
    id: str
    name: str


@dataclass
class DeviceCode:
    user_code: str
    url: str
    expires_in: int
    interval: int


@dataclass
class Credential:
    access: str
    refresh: str = ""
    expires: int = 0
    org: str = ""
    account: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Credential:
        expires = int(data.get("expires") or 0)
        # OpenCode stores milliseconds; a seconds value is converted once.
        if 0 < expires < 10_000_000_000:
            expires *= 1000
        return cls(
            access=str(data.get("access") or data.get("access_token") or ""),
            refresh=str(data.get("refresh") or data.get("refresh_token") or ""),
            expires=expires,
            org=str(data.get("org") or data.get("orgID") or ""),
            account=str(data.get("account") or ""),
        )

    def to_dict(self) -> dict:
        data = {
            "type": "oauth",
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
        }
        if self.org:
            data["org"] = self.org
        if self.account:
            data["account"] = self.account
        return data


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path or auth_path()

    def load(self) -> dict[str, dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.path)

    def get(self, provider: str = PROVIDER) -> dict | None:
        value = self.load().get(provider)
        return value if isinstance(value, dict) else None

    def set(self, provider: str, credential: dict) -> None:
        data = self.load()
        data[provider] = credential
        self.save(data)

    def remove(self, provider: str = PROVIDER) -> bool:
        data = self.load()
        if provider not in data:
            return False
        del data[provider]
        self.save(data)
        return True

    def status(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for provider, value in self.load().items():
            if not isinstance(value, dict):
                continue
            result[provider] = {
                "type": value.get("type", "api"),
                "expires": value.get("expires", 0),
                "org": value.get("org") or value.get("orgID") or "",
                "account": value.get("account", ""),
            }
        return result


class ZenAuth:
    """OpenCode Zen device-code login and token refresh."""

    def __init__(
        self,
        store: CredentialStore | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.store = store or CredentialStore()
        self.transport = transport

    def _client(self, *, token: str | None = None) -> httpx.AsyncClient:
        headers = {"User-Agent": CONSOLE_USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            base_url=CONSOLE,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=20.0),
            transport=self.transport,
        )

    async def device_login(
        self,
        *,
        on_code: Callable[[DeviceCode], None] | None = None,
        select_org: Callable[[list[Org]], Awaitable[str]] | None = None,
        open_browser: bool = True,
        org_id: str | None = None,
    ) -> Credential:
        async with self._client() as client:
            response = await client.post("/auth/device/code", json={"client_id": CLIENT_ID})
            if response.status_code >= 400:
                raise AuthError(f"Could not start login: HTTP {response.status_code}")
            start = response.json()
            url = start.get("verification_uri_complete") or start.get("verification_uri") or ""
            if url:
                url = urljoin(CONSOLE + "/", url)
            code = DeviceCode(
                user_code=str(start.get("user_code", "")),
                url=url,
                expires_in=int(start.get("expires_in") or 600),
                interval=int(start.get("interval") or 5),
            )
            if on_code:
                on_code(code)
            if open_browser and code.url:
                try:
                    webbrowser.open(code.url)
                except Exception:
                    pass

            interval = max(1.0, float(code.interval))
            deadline = time.time() + code.expires_in
            while time.time() < deadline:
                await asyncio.sleep(interval)
                poll = await client.post(
                    "/auth/device/token",
                    json={
                        "client_id": CLIENT_ID,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": start.get("device_code", ""),
                    },
                )
                try:
                    data = poll.json()
                except ValueError:
                    data = {}
                if data.get("access_token"):
                    credential = self._credential_from_token(data)
                    orgs = await self._fetch_orgs(client, credential.access)
                    chosen = org_id
                    if not chosen:
                        if len(orgs) == 1:
                            chosen = orgs[0].id
                        elif len(orgs) > 1 and select_org:
                            chosen = await select_org(orgs)
                        elif orgs:
                            chosen = orgs[0].id
                    credential.org = chosen or ""
                    self.store.set(PROVIDER, credential.to_dict())
                    return credential
                error = str(data.get("error") or "")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval += 5.0
                    continue
                detail = data.get("error_description") or error or f"HTTP {poll.status_code}"
                raise AuthError(f"Login failed: {detail}")
            raise AuthError("The device code expired before approval.")

    async def _fetch_orgs(self, client: httpx.AsyncClient, token: str) -> list[Org]:
        try:
            response = await client.get(
                "/api/orgs", headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code >= 400:
                return []
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        items = data if isinstance(data, list) else data.get("orgs", []) if isinstance(data, dict) else []
        orgs: list[Org] = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                orgs.append(Org(id=str(item["id"]), name=str(item.get("name") or item["id"])))
        return orgs

    @staticmethod
    def _credential_from_token(data: dict) -> Credential:
        expires_in = int(data.get("expires_in") or 3600)
        return Credential(
            access=str(data.get("access_token") or ""),
            refresh=str(data.get("refresh_token") or ""),
            expires=int(time.time() * 1000) + expires_in * 1000 - 5 * 60 * 1000,
        )

    async def refresh(self, credential: Credential) -> Credential:
        if not credential.refresh:
            raise AuthError("The session expired and there is no refresh token.")
        async with self._client() as client:
            response = await client.post(
                "/auth/device/token",
                json={
                    "client_id": CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh,
                },
            )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not data.get("access_token"):
            raise AuthError(
                str(data.get("error_description") or data.get("error") or "Token refresh failed.")
            )
        next_credential = self._credential_from_token(data)
        next_credential.org = credential.org
        next_credential.account = credential.account
        self.store.set(PROVIDER, next_credential.to_dict())
        return next_credential

    async def access(self) -> Credential:
        data = self.store.get(PROVIDER)
        if not data:
            raise AuthError("Not logged in. Run: aircode auth login")
        credential = Credential.from_dict(data)
        if not credential.access:
            raise AuthError("The stored credential is incomplete. Run: aircode auth login")
        now = int(time.time() * 1000)
        if credential.expires and credential.expires <= now + 60_000:
            if not credential.refresh:
                raise AuthError("The session expired. Run: aircode auth login")
            credential = await self.refresh(credential)
        return credential

    def logged_in(self) -> bool:
        data = self.store.get(PROVIDER)
        return bool(data and data.get("access"))

    def logout(self) -> bool:
        return self.store.remove(PROVIDER)

    def import_opencode(self, *, overwrite: bool = False, path: Path | None = None) -> list[str]:
        source = path or import_opencode_auth_path()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError(f"Cannot read {source}: {exc}") from exc
        if not isinstance(data, dict):
            raise AuthError(f"{source} does not contain a credential object.")
        current = self.store.load()
        imported: list[str] = []
        for provider, value in data.items():
            if not isinstance(value, dict):
                continue
            if provider in current and not overwrite:
                continue
            current[provider] = value
            imported.append(provider)
        if imported:
            self.store.save(current)
        return imported
