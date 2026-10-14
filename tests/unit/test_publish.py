"""``publish()`` on ``/v1``: the error mapping per mode and the CLI.

A 401 must not read the same for a wrong key, a wrong signing secret and a
missing consent. Each ``PublishError`` carries the mode, the wire reason and a
remedy specific to both.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from perceivo._cli import main as perceivo_cli
from perceivo._cli.publish_cmd import publish as publish_command
from perceivo.orchestration import publish
from perceivo.orchestration.publish import PublishError
from perceivo.types import (
    AccessDeniedError,
    AuthenticationError,
    Credentials,
    ServiceError,
)

from .fixtures_public_api import ASSET_ID, KEY_ID, SIGNING_SECRET

MODES = {
    "api_key": lambda: Credentials.api_key("k"),
    "hmac": lambda: Credentials.hmac("k", KEY_ID, SIGNING_SECRET),
    "entra": lambda: Credentials.entra("t", "c", client_secret="s"),
}

#: The platform API service's public reasons: the reasons an HMAC 401 may name on the wire.
PUBLIC_REASONS = (
    "signature_required",
    "invalid_signature",
    "timestamp_skew",
    "key_id_mismatch",
    "unsignable_target",
    "reserved_header_present",
    "nonce_format",
    "signature_malformed",
    "credential_in_query",
)


def _answer(api, status: int, body=None, **kwargs) -> None:
    api.api = lambda request: httpx.Response(status, json=body, **kwargs)


async def test_a_201_with_asset_id_is_the_orchestration_id(api, project):
    result = await publish(project, credentials=MODES["api_key"]())
    assert (result.orchestration_id, result.status_code) == (ASSET_ID, 201)
    assert str(api.api_requests[0].url) == "https://api.perceivo.ai/v1/orchestrations/import"


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_a_bare_401_is_an_authentication_error_with_a_mode_specific_remedy(
    api, project, mode
):
    _answer(api, 401, {"error": "unauthorized"})
    creds = MODES[mode]()
    with pytest.raises(AuthenticationError) as err:
        await publish(project, credentials=creds)
    assert (err.value.mode, err.value.status_code, err.value.reason) == (mode, 401, "unauthorized")
    assert err.value.remedy
    assert err.value.remedy in str(err.value)


def test_the_three_default_401_remedies_differ():
    from perceivo._internal.transport import _UNAUTHORIZED

    assert len(set(_UNAUTHORIZED.values())) == 3


@pytest.mark.parametrize("reason", PUBLIC_REASONS)
async def test_every_public_hmac_reason_maps_to_its_own_remedy(api, project, reason):
    _answer(api, 401, {"error": "unauthorized", "reason": reason})
    creds = MODES["hmac"]()
    with pytest.raises(AuthenticationError) as err:
        await publish(project, credentials=creds)
    assert (err.value.mode, err.value.reason) == ("hmac", reason)
    assert err.value.remedy
    from perceivo._internal.transport import _REASONS

    assert err.value.remedy == _REASONS[reason]


def test_the_reason_remedies_are_distinct():
    from perceivo._internal.transport import _REASONS

    assert set(_REASONS) == set(PUBLIC_REASONS)
    assert len(set(_REASONS.values())) == len(PUBLIC_REASONS)


@pytest.mark.parametrize("mode", sorted(MODES))
async def test_a_403_is_access_denied_naming_what_to_check(api, project, mode):
    _answer(api, 403, {"error": "forbidden"})
    creds = MODES[mode]()
    with pytest.raises(AccessDeniedError) as err:
        await publish(project, credentials=creds)
    assert (err.value.mode, err.value.status_code) == (mode, 403)
    for thing in ("ORCHESTRATION_CREATE", "COMPONENT_CREATE", "IP allowlist", "binding is enabled"):
        assert thing in err.value.remedy


async def test_a_413_is_a_publish_error_stating_the_hmac_cap(api, project):
    _answer(api, 413, {"error": "payload_too_large", "reason": "body_too_large"})
    creds = MODES["hmac"]()
    with pytest.raises(PublishError) as err:
        await publish(project, credentials=creds)
    assert not isinstance(err.value, AuthenticationError)
    assert err.value.status_code == 413
    assert "1 MiB" in err.value.remedy
    assert "no such limit" in err.value.remedy
    assert err.value.remedy in str(err.value)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_5xx_is_a_service_error(api, project, status):
    _answer(api, status, {"error": "internal_error"})
    creds = MODES["api_key"]()
    with pytest.raises(ServiceError) as err:
        await publish(project, credentials=creds)
    assert err.value.status_code == status
    assert "ServiceError(" in repr(err.value)


async def test_a_400_with_detail_still_lists_every_check(api, project):
    detail = {"errors": [{"code": "ORCH-PHANTOM-001", "message": "dangling"}]}
    _answer(api, 400, {"detail": detail})
    creds = MODES["api_key"]()
    with pytest.raises(PublishError) as err:
        await publish(project, credentials=creds)
    assert [c["id"] for c in err.value.checks] == ["ORCH-PHANTOM-001"]


async def test_a_2xx_without_asset_id_is_refused(api, project):
    _answer(api, 201, {"id": "b"})
    creds = MODES["api_key"]()
    creds = creds
    with pytest.raises(PublishError, match="no asset_id"):
        await publish(project, credentials=creds)


def test_publish_takes_credentials_and_neither_api_key_nor_import_path():
    params = inspect.signature(publish).parameters
    assert "credentials" in params
    assert "api_key" not in params
    assert "import_path" not in params


# --- the CLI ------------------------------------------------------------------------


def test_the_cli_has_no_api_key_flag_and_no_env_fallback_on_base_url():
    options = {param.name: param for param in publish_command.params}
    assert "api_key" not in options
    assert all("--api-key" not in param.opts for param in publish_command.params)
    assert options["base_url"].envvar is None


def test_the_cli_reports_an_auth_failure_with_its_remedy_and_no_traceback(
    api, project, monkeypatch
):
    monkeypatch.setenv("PERCEIVO_API_KEY", "k")
    _answer(api, 401, {"error": "unauthorized", "reason": "signature_required"})
    result = CliRunner().invoke(perceivo_cli, ["publish", project])
    assert result.exit_code == 1, result.output
    assert "PERCEIVO_KEY_ID" in result.output
    assert "Traceback" not in result.output


def test_the_cli_reports_a_missing_credential(api, project):
    result = CliRunner().invoke(perceivo_cli, ["publish", project])
    assert result.exit_code == 1, result.output
    assert "No public-API credential" in result.output
    assert api.requests == []


def test_the_cli_reads_the_base_url_from_env_through_the_resolver(api, project, monkeypatch):
    monkeypatch.setenv("PERCEIVO_API_KEY", "k")
    monkeypatch.setenv("PERCEIVO_API_URL", "http://127.0.0.1:9")
    result = CliRunner().invoke(perceivo_cli, ["publish", project])
    assert result.exit_code == 0, result.output
    assert str(api.api_requests[0].url) == "http://127.0.0.1:9/v1/orchestrations/import"


# --- nothing ships the /api/v1 prefix or a non-production host ---------------------

_PACKAGE = Path(__file__).resolve().parents[2] / "perceivo"
_VENDORED = _PACKAGE / "_internal" / "signing"
_HOSTS = ("api.dev.", "api.test.")


def _shipped() -> list[Path]:
    return [p for p in sorted(_PACKAGE.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


def _executable_strings(path: Path) -> list[tuple[int, str]]:
    """Every non-docstring string constant in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_shipped_file_names_a_dev_or_test_host():
    files = _shipped()
    assert len(files) > 100  # the population, so an empty scan cannot read as clean
    offenders = [
        f"{p.relative_to(_PACKAGE)}: {host}"
        for p in files
        for host in _HOSTS
        if host.encode() in p.read_bytes()
    ]
    assert offenders == []


def test_no_hand_written_shipped_file_names_the_api_v1_prefix():
    hand_written = [p for p in _shipped() if _VENDORED not in p.parents]
    assert [str(p) for p in hand_written if b"/api/v1" in p.read_bytes()] == []


def test_no_vendored_module_uses_the_api_v1_prefix_in_code():
    """The vendored signer's docstrings and its golden vectors cite ``/api/v1`` targets
    as upstream prose and test data, and stay byte-for-byte; no executable string
    in it may name the path."""
    modules = sorted(_VENDORED.glob("*.py"))
    assert len(modules) == 11
    found = [
        (m.name, line)
        for m in modules
        for line, value in _executable_strings(m)
        if "/api/v1" in value or any(h in value for h in _HOSTS)
    ]
    assert found == []
