"""Fixtures for the public-API transport tests: no network, a clean credential
environment, and an httpx ``MockTransport`` that captures every request.

Every request the SDK builds goes through ``httpx.AsyncClient``, so replacing that
one name with a subclass that forces a ``MockTransport`` captures the requests
exactly as they would have been sent -- headers, bytes and all -- with no seam in
shipped code. Sockets are blocked as well, so a request that escaped the mock
fails loudly instead of reaching a real host.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from perceivo._internal import _entra

#: Every variable the resolver reads. Cleared so a developer's shell cannot leak in.
CREDENTIAL_ENV = (
    "PERCEIVO_API_URL",
    "PERCEIVO_API_KEY",
    "PERCEIVO_KEY_ID",
    "PERCEIVO_SIGNING_SECRET",
    "PERCEIVO_SIGNING_AUDIENCE",
    "PERCEIVO_TENANT_ID",
    "PERCEIVO_CLIENT_ID",
    "PERCEIVO_CLIENT_SECRET",
    "PERCEIVO_FEDERATED_TOKEN_FILE",
    "PERCEIVO_TOKEN_SCOPE",
    "PERCEIVO_CONFIG",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_FEDERATED_TOKEN_FILE",
)

TOKEN_HOST = "login.microsoftonline.com"
ASSET_ID = "5b0c9d2e-0000-4000-8000-000000000001"
#: A published test signing secret (64 hex) -- the shape the platform discloses.
SIGNING_SECRET = "0f1e2d3c4b5a69788796a5b4c3d2e1f00123456789abcdeffedcba9876543210"
KEY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def minted_token(request: httpx.Request) -> str:
    """The token the mock identity platform mints for one token request: a digest
    of the tenant path and every form field, so two credentials that differ in
    any of them get different tokens."""
    form = sorted(httpx.QueryParams(request.content.decode("utf-8")).multi_items())
    material = json.dumps([request.url.path, form]).encode("utf-8")
    return "tok-" + hashlib.sha256(material).hexdigest()[:24]


class Capture:
    """The requests sent, and the answer each one gets."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.client_kwargs: list[dict[str, Any]] = []
        self.token_lifetime = 3600
        self.api: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            201, json={"asset_id": ASSET_ID}
        )
        self.token: Callable[[httpx.Request], httpx.Response] = self._mint

    def _mint(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": minted_token(request), "expires_in": self.token_lifetime}
        )

    async def slow_mint(self, request: httpx.Request) -> httpx.Response:
        """A token endpoint that suspends, so concurrent callers really overlap."""
        await asyncio.sleep(0.05)
        return self._mint(request)

    def handle(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        if request.url.host == TOKEN_HOST:
            return self.token(request)
        return self.api(request)

    @property
    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host != TOKEN_HOST]

    @property
    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == TOKEN_HOST]

    def token_form(self, index: int = 0) -> dict[str, str]:
        return dict(httpx.QueryParams(self.token_requests[index].content.decode("utf-8")))

    def minted(self, index: int = 0) -> str:
        return minted_token(self.token_requests[index])


def _is_loopback(address: Any) -> bool:
    """Loopback stays open: the Windows event loop builds its self-pipe from a
    connected loopback socket pair. Nothing else does."""
    host = address[0] if isinstance(address, tuple) and address else None
    return host in ("127.0.0.1", "::1")


def _guard(original: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(sock: socket.socket, address: Any, *args: Any) -> Any:
        if not _is_loopback(address):
            raise AssertionError(f"a unit test tried to open a network connection to {address}")
        return original(sock, address, *args)

    return guarded


def _refuse_create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
    raise AssertionError(f"a unit test tried to open a network connection to {address}")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection", _refuse_create_connection)


@pytest.fixture
def clean_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """No credential variable set, no token cached, and the cwd an empty directory."""
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    _entra.clear_cache()
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    yield work
    _entra.clear_cache()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, no_network: None, clean_credentials: Path) -> Capture:
    """Capture every request any ``httpx.AsyncClient`` sends."""
    capture = Capture()
    real = httpx.AsyncClient

    class _Captured(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            capture.client_kwargs.append(dict(kwargs))
            kwargs["transport"] = httpx.MockTransport(capture.handle)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Captured)
    return capture


def write_project(directory: Path) -> str:
    """A minimal START -> END orchestration project, as the builder saves it."""
    from perceivo.orchestration._builder import Orchestration

    orchestration = Orchestration("pub")
    start = orchestration.add_node("start", "START")
    end = orchestration.add_node("end", "END")
    orchestration.add_edge(start, end)
    orchestration.save(str(directory))
    return str(directory)


@pytest.fixture
def project(tmp_path: Path) -> str:
    return write_project(tmp_path / "proj")
