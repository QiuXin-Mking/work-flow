"""Persistent service OAuth for the official Feishu Project MCP server."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


MCP_URL = "https://project.feishu.cn/mcp_server/v1"
REGISTER_URL = "https://project.feishu.cn/mcp_server/oauth/register"
DEVICE_URL = "https://project.feishu.cn/goapi/v5/mcp_server/oauth/device_code"
TOKEN_URL = "https://project.feishu.cn/mcp_server/oauth/token"
DEFAULT_STORE = "/data/feishu-mcp-oauth.json"


class OAuthStore:
    def __init__(self, path: str | Path = DEFAULT_STORE) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Invalid Feishu MCP authorization file")
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".feishu-mcp-", dir=self.path.parent)
        try:
            os.chmod(name, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class ServiceOAuth:
    """Refresh a single server-side OAuth identity shared by all local users."""

    def __init__(self, store: OAuthStore | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.store = store or OAuthStore(os.getenv("FEISHU_MCP_AUTH_FILE", DEFAULT_STORE))
        self.transport = transport
        self._lock = threading.Lock()

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=15,
                            follow_redirects=False, trust_env=False)

    def access_token(self) -> str:
        with self._lock:
            data = self.store.load()
            token = data.get("access_token")
            expiry = data.get("expires_at")
            if isinstance(token, str) and token and isinstance(expiry, (int, float)) \
                    and expiry > time.time() + 60:
                return token
            if not data.get("client_id") or not data.get("refresh_token"):
                raise ValueError("Feishu MCP authorization is required; run the device login command")
            form = {"grant_type": "refresh_token", "client_id": data["client_id"],
                    "refresh_token": data["refresh_token"]}
            if data.get("client_secret"):
                form["client_secret"] = data["client_secret"]
            with self._client() as client:
                response = client.post(TOKEN_URL, data=form)
            if response.status_code != 200:
                raise ValueError("Feishu MCP authorization expired; run the device login command")
            payload = response.json()
            access = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(access, str) or not access:
                raise ValueError("Feishu MCP token refresh returned no access token")
            data.update(access_token=access,
                        refresh_token=payload.get("refresh_token") or data["refresh_token"],
                        expires_at=time.time() + int(payload.get("expires_in", 3600)))
            self.store.save(data)
            return access

    def begin_device_login(self) -> dict[str, Any]:
        """Register the service client and request a short-lived device code."""
        data = self.store.load()
        with self._client() as client:
            def register_client() -> dict[str, Any]:
                response = client.post(REGISTER_URL, json={
                    "client_name": "Pi Bugfix Console",
                    "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
                    "response_types": [],
                    "token_endpoint_auth_method": "client_secret_post",
                })
                response.raise_for_status()
                registration = response.json()
                client_id = registration.get("client_id")
                if not isinstance(client_id, str) or not client_id:
                    raise ValueError("Feishu MCP client registration failed")
                registered = {"client_id": client_id}
                if isinstance(registration.get("client_secret"), str):
                    registered["client_secret"] = registration["client_secret"]
                self.store.save(registered)
                return registered

            def request_code(registered: dict[str, Any]) -> dict[str, Any]:
                form = {"client_id": registered["client_id"]}
                if registered.get("client_secret"):
                    form["client_secret"] = registered["client_secret"]
                response = client.post(DEVICE_URL, data=form)
                response.raise_for_status()
                return response.json()

            if not data.get("client_id"):
                data = register_client()
            challenge = request_code(data)
            if challenge.get("code") == 1000053834:
                data = register_client()
                challenge = request_code(data)
        uri = challenge.get("verification_uri") or challenge.get("verification_url")
        if (not isinstance(uri, str) or urlsplit(uri).scheme != "https"
                or urlsplit(uri).hostname not in {"project.feishu.cn", "www.feishu.cn", "open.feishu.cn"}
                or not isinstance(challenge.get("device_code"), str)
                or not isinstance(challenge.get("user_code"), str)):
            raise ValueError("Invalid Feishu MCP device authorization response")
        return challenge

    def finish_device_login(self, challenge: dict[str, Any],
                            *, sleep=time.sleep) -> None:
        deadline = time.monotonic() + min(int(challenge.get("expires_in", 600)), 900)
        interval = max(int(challenge.get("interval", 5)), 1)
        while time.monotonic() < deadline:
            sleep(interval)
            data = self.store.load()
            form = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": challenge["device_code"], "client_id": data["client_id"]}
            if data.get("client_secret"):
                form["client_secret"] = data["client_secret"]
            with self._client() as client:
                response = client.post(TOKEN_URL, data=form)
            payload = response.json()
            if response.status_code == 200 and isinstance(payload.get("access_token"), str):
                data.update(access_token=payload["access_token"],
                            refresh_token=payload.get("refresh_token"),
                            expires_at=time.time() + int(payload.get("expires_in", 3600)))
                if not data["refresh_token"]:
                    raise ValueError("Feishu MCP did not issue a refresh token")
                self.store.save(data)
                return
            error = payload.get("error") if isinstance(payload, dict) else None
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise ValueError(f"Feishu MCP device authorization failed: {error or response.status_code}")
        raise ValueError("Feishu MCP device authorization timed out")


def main() -> None:
    oauth = ServiceOAuth()
    challenge = oauth.begin_device_login()
    print(f"Open: {challenge.get('verification_uri') or challenge.get('verification_url')}", flush=True)
    print(f"Code: {challenge['user_code']}", flush=True)
    oauth.finish_device_login(challenge)
    print("Feishu MCP authorization saved for the backend.", flush=True)


if __name__ == "__main__":
    main()
