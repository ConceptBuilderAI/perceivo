"""Resolving exactly one authentication mode before any I/O.

Every refusal names each variable involved **and** where it came from, because
"conflicting credentials" with no names sends the user hunting through three
layers for the one they forgot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perceivo._internal import auth
from perceivo._internal.auth import (
    DEFAULT_API_URL,
    PROD_HMAC_AUDIENCE,
    PROD_TOKEN_SCOPE,
    AuthMode,
    MissingCredentialsError,
    resolve,
)
from perceivo.types import ConfigurationError, Credentials

pytestmark = pytest.mark.usefixtures("no_network", "clean_credentials")

KEY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
HEX = "ab" * 32


def _yaml(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "perceivo.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _set(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# --- one mode per layer ------------------------------------------------------


def test_api_key_from_env(monkeypatch):
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    resolved = resolve()
    assert (resolved.mode, resolved.credentials.key) == (AuthMode.API_KEY, "k")


def test_hmac_from_env_defaults_the_prod_audience(monkeypatch):
    _set(monkeypatch, PERCEIVO_API_KEY="k", PERCEIVO_KEY_ID=KEY_ID, PERCEIVO_SIGNING_SECRET=HEX)
    resolved = resolve()
    assert resolved.mode is AuthMode.HMAC
    assert resolved.credentials.audience == PROD_HMAC_AUDIENCE == "perceivo-api-prod"


def test_entra_from_env_defaults_the_prod_scope(monkeypatch):
    _set(monkeypatch, PERCEIVO_TENANT_ID="t", PERCEIVO_CLIENT_ID="c", PERCEIVO_CLIENT_SECRET="s")
    resolved = resolve()
    assert resolved.mode is AuthMode.ENTRA
    assert resolved.credentials.scope == PROD_TOKEN_SCOPE
    assert PROD_TOKEN_SCOPE == "api://bf151286-9548-4e40-af09-48523cdfc3e7/.default"


def test_entra_token_file_from_yaml_resolves_against_the_yaml_directory(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _yaml(project, "api:\n  tenant_id: t\n  client_id: c\n  federated_token_file: tok.txt\n")
    resolved = resolve(project_dir=project)
    assert resolved.credentials.federated_token_file == str((project / "tok.txt").resolve())


def test_hmac_non_secret_fields_merge_from_yaml(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _yaml(project, f"api:\n  key_id: {KEY_ID}\n  signing_audience: aud-x\n")
    _set(monkeypatch, PERCEIVO_API_KEY="k", PERCEIVO_SIGNING_SECRET=HEX)
    resolved = resolve(project_dir=project)
    assert resolved.mode is AuthMode.HMAC
    assert (resolved.credentials.key_id, resolved.credentials.audience) == (KEY_ID, "aud-x")
    assert resolved.base_url == DEFAULT_API_URL


def test_env_wins_over_yaml_field_by_field(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _yaml(project, "api:\n  signing_audience: from-yaml\n")
    _set(
        monkeypatch,
        PERCEIVO_API_KEY="k",
        PERCEIVO_KEY_ID=KEY_ID,
        PERCEIVO_SIGNING_SECRET=HEX,
        PERCEIVO_SIGNING_AUDIENCE="from-env",
        PERCEIVO_API_URL="https://env.test",
    )
    resolved = resolve(project_dir=project)
    assert resolved.credentials.audience == "from-env"
    assert resolved.base_url == "https://env.test"
    assert resolve(project_dir=project, base_url="https://arg.test").base_url == "https://arg.test"


def test_the_default_base_url_is_production(monkeypatch):
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    assert resolve().base_url == DEFAULT_API_URL == "https://api.perceivo.ai"


# --- every conflict refused, naming each variable and its source --------------

_ENTRA = {"PERCEIVO_TENANT_ID": "t", "PERCEIVO_CLIENT_ID": "c"}

CONFLICTS = [
    pytest.param(
        {"PERCEIVO_API_KEY": "k", **_ENTRA, "PERCEIVO_CLIENT_SECRET": "s"},
        ["PERCEIVO_API_KEY [env]", "PERCEIVO_TENANT_ID [env]", "PERCEIVO_CLIENT_SECRET [env]"],
        id="api-key-with-entra",
    ),
    pytest.param(
        {"PERCEIVO_API_KEY": "k", "PERCEIVO_KEY_ID": KEY_ID},
        ["PERCEIVO_KEY_ID [env]"],
        id="key-id-without-secret",
    ),
    pytest.param(
        {"PERCEIVO_API_KEY": "k", "PERCEIVO_SIGNING_SECRET": HEX},
        ["PERCEIVO_SIGNING_SECRET [env]"],
        id="secret-without-key-id",
    ),
    pytest.param(
        {"PERCEIVO_KEY_ID": KEY_ID, "PERCEIVO_SIGNING_SECRET": HEX},
        ["PERCEIVO_KEY_ID [env]", "PERCEIVO_SIGNING_SECRET [env]", "PERCEIVO_API_KEY"],
        id="hmac-without-api-key",
    ),
    pytest.param(
        {"PERCEIVO_TENANT_ID": "t", "PERCEIVO_CLIENT_SECRET": "s"},
        ["PERCEIVO_TENANT_ID [env]", "PERCEIVO_CLIENT_ID"],
        id="tenant-without-client",
    ),
    pytest.param(
        {"PERCEIVO_CLIENT_ID": "c", "PERCEIVO_CLIENT_SECRET": "s"},
        ["PERCEIVO_CLIENT_ID [env]", "PERCEIVO_TENANT_ID"],
        id="client-without-tenant",
    ),
    pytest.param(
        {"PERCEIVO_CLIENT_SECRET": "s"},
        ["PERCEIVO_CLIENT_SECRET [env]", "PERCEIVO_TENANT_ID"],
        id="entra-secret-alone",
    ),
    pytest.param(
        {**_ENTRA, "PERCEIVO_CLIENT_SECRET": "s", "PERCEIVO_FEDERATED_TOKEN_FILE": "/t"},
        ["PERCEIVO_CLIENT_SECRET [env]", "PERCEIVO_FEDERATED_TOKEN_FILE [env]"],
        id="secret-and-token-file",
    ),
    pytest.param(
        dict(_ENTRA),
        ["PERCEIVO_TENANT_ID [env]", "PERCEIVO_CLIENT_ID [env]", "PERCEIVO_CLIENT_SECRET"],
        id="entra-without-secret-or-file",
    ),
    pytest.param(
        {
            "PERCEIVO_API_URL": "https://staging.test",
            "PERCEIVO_API_KEY": "k",
            "PERCEIVO_KEY_ID": KEY_ID,
            "PERCEIVO_SIGNING_SECRET": HEX,
        },
        [
            "PERCEIVO_SIGNING_AUDIENCE",
            "PERCEIVO_KEY_ID [env]",
            "https://staging.test",
            "PERCEIVO_API_URL [env]",
        ],
        id="non-prod-hmac-without-audience",
    ),
    pytest.param(
        {"PERCEIVO_API_URL": "https://staging.test", **_ENTRA, "PERCEIVO_CLIENT_SECRET": "s"},
        [
            "PERCEIVO_TOKEN_SCOPE",
            "PERCEIVO_TENANT_ID [env]",
            "https://staging.test",
            "PERCEIVO_API_URL [env]",
        ],
        id="non-prod-entra-without-scope",
    ),
]


@pytest.mark.parametrize(("env", "named"), CONFLICTS)
def test_every_conflict_is_refused_naming_each_variable_and_its_source(monkeypatch, env, named):
    _set(monkeypatch, **env)
    with pytest.raises(ConfigurationError) as err:
        resolve()
    for fragment in named:
        assert fragment in str(err.value), (fragment, str(err.value))


def test_a_yaml_sourced_field_is_named_with_its_file(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    path = _yaml(project, "api:\n  tenant_id: t\n  client_id: c\n")
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=project)
    message = str(err.value)
    assert "PERCEIVO_API_KEY [env]" in message
    assert f"api.tenant_id [perceivo.yaml:{path}]" in message


def test_no_credential_at_all_is_a_missing_credentials_error_that_mentions_dotenv():
    with pytest.raises(MissingCredentialsError) as err:
        resolve()
    assert ".env" in str(err.value)
    assert "not for platform credentials" in str(err.value)
    assert isinstance(err.value, ConfigurationError)


def test_azure_variables_are_never_a_fallback(monkeypatch):
    _set(monkeypatch, AZURE_CLIENT_ID="c", AZURE_TENANT_ID="t", AZURE_FEDERATED_TOKEN_FILE="/f")
    with pytest.raises(MissingCredentialsError):
        resolve()


# --- explicit credentials win wholesale ----------------------------------------


def test_explicit_credentials_ignore_conflicting_env(monkeypatch):
    _set(monkeypatch, PERCEIVO_API_KEY="env-key", **_ENTRA)  # a conflict on its own
    resolved = resolve(credentials=Credentials.api_key("arg-key"))
    assert (resolved.mode, resolved.credentials.key) == (AuthMode.API_KEY, "arg-key")


def test_explicit_hmac_needs_an_audience_off_production():
    creds = Credentials.hmac("k", KEY_ID, HEX)
    with pytest.raises(ConfigurationError, match="audience") as err:
        resolve(credentials=creds, base_url="https://staging.test")
    # The URL's source is named, so the user knows which layer chose the host.
    assert "base_url=/--base-url [argument]" in str(err.value)
    ok = resolve(
        credentials=Credentials.hmac("k", KEY_ID, HEX, audience="a"), base_url="https://s.test"
    )
    assert ok.credentials.audience == "a"


def test_explicit_entra_needs_a_scope_off_production():
    creds = Credentials.entra("t", "c", client_secret="s")
    with pytest.raises(ConfigurationError, match="scope") as err:
        resolve(credentials=creds, base_url="https://staging.test")
    assert "base_url=/--base-url [argument]" in str(err.value)


def test_a_non_credentials_object_is_refused():
    with pytest.raises(ConfigurationError):
        resolve(credentials="k")  # type: ignore[arg-type]


# --- transport safety of the base URL -----------------------------------------


def _resolve_url_from(source: str, url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Resolve with ``url`` supplied by the argument or by ``PERCEIVO_API_URL``."""
    if source == "env":
        monkeypatch.setenv("PERCEIVO_API_URL", url)
        return resolve().base_url
    return resolve(base_url=url).base_url


_URL_SOURCES = pytest.mark.parametrize("source", ["argument", "env"])


@_URL_SOURCES
@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "ftp://x.test",
        "https://",
        "https://x.test/?a=1",
        "https://api.perceivo.ai/api",
        "https://x.test/v1",
        # A name that merely starts like a loopback host is not one.
        "http://localhost.evil.com",
        "http://localhost.evil.com:8000",
        "http://127.0.0.1.evil.com",
    ],
)
def test_an_unsafe_base_url_is_refused(monkeypatch, source, url):
    """Every source goes through the same check: ``PERCEIVO_API_URL=http://...``
    would otherwise send the credential in clear."""
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    with pytest.raises(ConfigurationError, match="the API base URL"):
        _resolve_url_from(source, url, monkeypatch)


@_URL_SOURCES
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://LOCALHOST:8000",
        "http://[::1]:1",
        "https://x.test",
    ],
)
def test_a_safe_base_url_is_accepted_from_every_source(monkeypatch, source, url):
    """The control of the test above: the same harness accepts a safe URL, so the
    refusals there are the URL check's and not the harness's."""
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    assert _resolve_url_from(source, url, monkeypatch) == url


@_URL_SOURCES
def test_a_base_url_with_a_path_is_refused_naming_why(monkeypatch, source):
    _set(monkeypatch, PERCEIVO_API_KEY="k")
    with pytest.raises(ConfigurationError, match="must not carry a path"):
        _resolve_url_from(source, "https://api.perceivo.ai/api", monkeypatch)
    monkeypatch.delenv("PERCEIVO_API_URL", raising=False)
    # Control: the bare host and a trailing slash are not paths.
    assert resolve(base_url="https://api.perceivo.ai/").base_url == DEFAULT_API_URL
    assert resolve(base_url="https://x.test").base_url == "https://x.test"


def test_pick_first_is_not_how_conflicts_resolve(monkeypatch):
    """The mutant this guards: a resolver that returns the first complete mode."""
    _set(monkeypatch, PERCEIVO_API_KEY="k", **_ENTRA, PERCEIVO_CLIENT_SECRET="s")
    with pytest.raises(ConfigurationError):
        auth.resolve()
