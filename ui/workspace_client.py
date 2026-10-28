"""Gateway client for the part workspace (Rule g: the UI talks to the API gateway only).

No mock fallback: the workspace shows what the gateway says. Tests swap the
transport for an in-process FastAPI TestClient with `set_transport`.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from typing import Any

import requests

API_URL = os.environ.get("PARTPROOF_API_URL", "http://localhost:8000")
_TIMEOUT = 30

_transport: Any = None  # requests.Session with a base URL, or a TestClient


def set_transport(transport: Any) -> None:
    """Use a different HTTP transport (an in-process TestClient in tests)."""
    global _transport
    _transport = transport


class _RequestsTransport:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def request(self, method: str, path: str, **kwargs):
        return self.session.request(method, self.base_url + path, timeout=_TIMEOUT, **kwargs)

    def stream_lines(self, method: str, path: str, **kwargs) -> Iterator[str]:
        with self.session.request(method, self.base_url + path, stream=True, timeout=_TIMEOUT, **kwargs) as r:
            r.raise_for_status()
            yield from (line for line in r.iter_lines(decode_unicode=True) if line)


class _TestClientTransport:
    def __init__(self, client):
        self.client = client

    def request(self, method: str, path: str, **kwargs):
        return self.client.request(method, path, **kwargs)

    def stream_lines(self, method: str, path: str, **kwargs) -> Iterator[str]:
        with self.client.stream(method, path, **kwargs) as r:
            r.raise_for_status()
            yield from (line for line in r.iter_lines() if line)


def _make_transport():
    if _transport is None:
        return _RequestsTransport(API_URL)
    return _transport if hasattr(_transport, "stream_lines") else _TestClientTransport(_transport)


class GatewayError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class WorkspaceClient:
    """One logged-in seller's view of the gateway."""

    def __init__(self, token: str | None = None):
        self.http = _make_transport()
        self.token = token

    # ── plumbing ──

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _call(self, method: str, path: str, **kwargs) -> Any:
        r = self.http.request(method, path, headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise GatewayError(r.status_code, detail)
        return r.json() if r.content else None

    # ── login ──

    def profiles(self) -> list[dict]:
        return self._call("GET", "/profiles")["profiles"]

    def login(self, display_name: str) -> dict:
        data = self._call("POST", "/sessions", json={"display_name": display_name})
        self.token = data["token"]
        return data

    def logout(self) -> None:
        if self.token:
            self._call("DELETE", "/sessions")
        self.token = None

    def profile(self) -> dict:
        return self._call("GET", "/me/profile")

    def update_profile(self, changes: dict) -> dict:
        return self._call("PUT", "/me/profile", json=changes)

    # ── board and pipeline ──

    def board(self) -> list[dict]:
        return self._call("GET", "/me/board")["parts"]

    def metrics(self) -> dict:
        return self._call("GET", "/me/metrics")["metrics"]

    def run_pipeline(self, wait: bool = False) -> list[str]:
        return self._call("POST", f"/me/pipeline/run?wait={'true' if wait else 'false'}")["queued"]

    def pipeline(self, sku: str) -> dict:
        return self._call("GET", f"/me/pipeline/{sku}")["pipeline"]

    def quarantine(self) -> list[dict]:
        return self._call("GET", "/me/quarantine")["quarantine"]

    # ── one part ──

    def detail(self, sku: str) -> dict:
        return self._call("GET", f"/me/parts/{sku}/detail")

    def photo(self, sku: str, evidence_id: str) -> bytes:
        r = self.http.request("GET", f"/me/parts/{sku}/photos/{evidence_id}", headers=self._headers())
        if r.status_code >= 400:
            raise GatewayError(r.status_code, "photo not found")
        return r.content

    def upload_photo(self, sku: str, filename: str, content: bytes) -> dict:
        body = {"filename": filename, "content_b64": base64.b64encode(content).decode("ascii")}
        return self._call("POST", f"/me/parts/{sku}/photos", json=body)["upload"]

    def draft(self, sku: str) -> dict | None:
        try:
            return self._call("GET", f"/me/parts/{sku}/draft")["draft"]
        except GatewayError as exc:
            if exc.status == 404:
                return None
            raise

    def edit_draft(self, sku: str, changes: dict) -> dict:
        return self._call("PUT", f"/me/parts/{sku}/draft", json=changes)

    def readiness(self, sku: str) -> dict:
        return self._call("GET", f"/me/parts/{sku}/readiness")

    def review(self, sku: str) -> dict:
        return self._call("GET", f"/me/parts/{sku}/review")["review"]

    def history(self, sku: str) -> list[dict]:
        return self._call("GET", f"/me/parts/{sku}/history")["history"]

    def approve(self, sku: str) -> dict:
        return self._call("POST", f"/me/parts/{sku}/approve")["pipeline"]

    # ── chat ──

    def chat(self, message: str, sku: str | None) -> dict:
        return self._call("POST", "/me/chat", json={"message": message, "sku": sku})

    def stream_chat(self, message: str, sku: str | None) -> Iterator[dict]:
        """NDJSON events from the gateway: ack, token…, done (with the full reply)."""
        for line in self.http.stream_lines("POST", "/me/chat/stream", headers=self._headers(),
                                           json={"message": message, "sku": sku}):
            yield json.loads(line)
