"""No configuration file chooses the host a credential is sent to.

A ``perceivo.yaml`` found by discovery may be the project's own, a template's, or
any ancestor directory's, and ``publish`` runs no project code. If such a file could
set the API base URL, publishing an untrusted project with credentials in the
environment would send those credentials to whatever host the file named. So the
base URL comes only from ``base_url=``, ``--base-url`` or ``PERCEIVO_API_URL``, and
a file that names it is refused outright -- not silently ignored.

Each test carries a control that must come out the other way, so a harness that
refused everything, or recorded nothing, could not pass it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from perceivo import config as config_mod
from perceivo._cli import main as perceivo_cli
from perceivo._internal.auth import DEFAULT_API_URL, PROD_TOKEN_SCOPE, resolve
from perceivo._internal.config_discovery import TOP_LEVEL_URL_KEYS, URL_KEYS
from perceivo.orchestration import publish
from perceivo.types import ConfigurationError, Credentials

from .fixtures_public_api import KEY_ID, SIGNING_SECRET, TOKEN_HOST, Capture, write_project

pytestmark = pytest.mark.usefixtures("no_network", "clean_credentials")

EVIL = "https://evil.example"
EVIL_HOST = "evil.example"
API_KEY = "pk-live-SENTINEL-api-key"
CLIENT_SECRET = "entra-SENTINEL-client-secret"

ENV_MODES = {
    "api_key": {"PERCEIVO_API_KEY": API_KEY},
    "hmac": {
        "PERCEIVO_API_KEY": API_KEY,
        "PERCEIVO_KEY_ID": KEY_ID,
        "PERCEIVO_SIGNING_SECRET": SIGNING_SECRET,
    },
    "entra": {
        "PERCEIVO_TENANT_ID": "tenant-1",
        "PERCEIVO_CLIENT_ID": "client-1",
        "PERCEIVO_CLIENT_SECRET": CLIENT_SECRET,
    },
}


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _write(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "perceivo.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _hostile(directory: Path, key: str = "url", extra: str = "") -> Path:
    return _write(directory, f"api:\n  {key}: {EVIL}\n{extra}")


def _assert_refused_naming(err: pytest.ExceptionInfo[ConfigurationError], key: str, path: Path):
    message = str(err.value)
    assert key in message, message
    assert str(path) in message, message
    assert "PERCEIVO_API_URL" in message, message  # the replacement is named
    assert EVIL not in message and EVIL_HOST not in message, message  # the value is not


def _assert_nothing_reached(capture: Capture) -> None:
    """No request at all: not to the file's host, not to the default, not for a token."""
    hosts = [request.url.host for request in capture.requests]
    assert hosts == [], hosts


# --- a file naming the base URL is refused, naming the key and the file ---------------


@pytest.mark.parametrize("key", sorted(URL_KEYS))
def test_an_api_url_key_in_a_discovered_file_is_refused(tmp_path, monkeypatch, key):
    _set(monkeypatch, ENV_MODES["api_key"])
    project = tmp_path / "proj"
    path = _hostile(project, key)
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=project)
    _assert_refused_naming(err, f"api.{key}", path)
    # Control: the same file without that key resolves, so the refusal is the key's.
    _write(project, "api:\n  key_id: kid\n")
    with pytest.raises(ConfigurationError, match="HMAC signing needs both"):
        resolve(project_dir=project)  # a different, key_id-driven error: the file loaded
    _write(project, "api: {}\n")
    assert resolve(project_dir=project).base_url == DEFAULT_API_URL


@pytest.mark.parametrize("key", [*sorted(TOP_LEVEL_URL_KEYS), "PERCEIVO_API_URL", "Base_Url"])
def test_a_top_level_url_key_is_refused(tmp_path, monkeypatch, key):
    _set(monkeypatch, ENV_MODES["api_key"])
    project = tmp_path / "proj"
    path = _write(project, f"{key}: {EVIL}\n")
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=project)
    _assert_refused_naming(err, key, path)
    # Control: an unrelated top-level key is not refused.
    _write(project, f"redis:\n  url: {EVIL}\n")
    assert resolve(project_dir=project).base_url == DEFAULT_API_URL


def test_config_load_refuses_the_same_file(tmp_path):
    path = _hostile(tmp_path)
    with pytest.raises(ConfigurationError) as err:
        config_mod.load(str(path))
    _assert_refused_naming(err, "api.url", path)
    top = _write(tmp_path / "top", f"base_url: {EVIL}\n")
    with pytest.raises(ConfigurationError, match=r"base_url is refused"):
        config_mod.load(str(top))
    # Control: a file with the other api: keys loads.
    ok = _write(tmp_path / "ok", "api:\n  key_id: kid\n")
    assert config_mod.load(str(ok)).api.key_id == "kid"


def test_an_ancestor_file_found_by_the_upward_walk_is_refused(tmp_path, monkeypatch):
    _set(monkeypatch, ENV_MODES["api_key"])
    path = _hostile(tmp_path / "root")
    project = tmp_path / "root" / "shared" / "proj"
    project.mkdir(parents=True)
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=project)
    _assert_refused_naming(err, "api.url", path)
    # Control: a project outside that ancestor is unaffected.
    elsewhere = tmp_path / "other" / "proj"
    elsewhere.mkdir(parents=True)
    assert resolve(project_dir=elsewhere).base_url == DEFAULT_API_URL


def test_a_file_named_by_perceivo_config_is_refused(tmp_path, monkeypatch):
    _set(monkeypatch, ENV_MODES["api_key"])
    path = _hostile(tmp_path / "named")
    monkeypatch.setenv("PERCEIVO_CONFIG", str(path))
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=tmp_path / "proj")
    _assert_refused_naming(err, "api.url", path)


@pytest.mark.parametrize(
    "operator", [{"env": DEFAULT_API_URL}, {"arg": DEFAULT_API_URL}, {"env": EVIL, "arg": EVIL}]
)
def test_an_operator_url_does_not_legitimise_the_file(tmp_path, monkeypatch, operator):
    """Refused even when the operator names a URL too -- the same one or not."""
    _set(monkeypatch, ENV_MODES["api_key"])
    if "env" in operator:
        monkeypatch.setenv("PERCEIVO_API_URL", operator["env"])
    project = tmp_path / "proj"
    _hostile(project)
    with pytest.raises(ConfigurationError, match=r"api.url is refused"):
        resolve(project_dir=project, base_url=operator.get("arg"))


def test_the_refused_key_lists_are_the_reviewed_ones():
    """The parametrized tests above iterate over the constants they guard, so a key
    dropped from a constant would drop its own test. These literals pin them."""
    assert {"url", "base_url", "api_url", "endpoint", "host", "uri", "server"} == URL_KEYS
    assert {"api_url", "base_url", "perceivo_api_url"} == TOP_LEVEL_URL_KEYS


@pytest.mark.parametrize("key", ["Url", "ENDPOINT", "Base_URL"])
def test_an_api_url_key_is_refused_whatever_its_case(tmp_path, monkeypatch, key):
    _set(monkeypatch, ENV_MODES["api_key"])
    project = tmp_path / "proj"
    path = _hostile(project, key)
    with pytest.raises(ConfigurationError) as err:
        resolve(project_dir=project)
    _assert_refused_naming(err, f"api.{key}", path)


@pytest.mark.parametrize("mode", sorted(ENV_MODES))
async def test_explicit_credentials_do_not_skip_the_file_refusal(api, tmp_path, mode):
    """``credentials=`` decides the mode on its own, but the file is still read and
    still refused: an explicit credential must not be the one case a hostile file
    gets past."""
    explicit = {
        "api_key": lambda: Credentials.api_key(API_KEY),
        "hmac": lambda: Credentials.hmac(API_KEY, KEY_ID, SIGNING_SECRET),
        "entra": lambda: Credentials.entra("tenant-1", "client-1", client_secret=CLIENT_SECRET),
    }[mode]()
    project = write_project(tmp_path / "proj")
    path = _hostile(Path(project))
    with pytest.raises(ConfigurationError) as err:
        await publish(project, credentials=explicit)
    _assert_refused_naming(err, "api.url", path)
    _assert_nothing_reached(api)
    # Control: the same explicit credential with a clean file publishes to the default.
    _write(Path(project), "api: {}\n")
    await publish(project, credentials=explicit)
    assert [r.url.host for r in api.api_requests] == ["api.perceivo.ai"]


# --- credentials are never sent to a URL that came from a file -----------------------


@pytest.mark.parametrize("mode", sorted(ENV_MODES))
async def test_env_credentials_are_never_sent_to_a_file_url(api, tmp_path, monkeypatch, mode):
    """The attack: a hostile ``perceivo.yaml`` plus credentials in the environment.

    For Entra, the file also names the production scope, which is what would have
    let it mint a production bearer. Nothing may be requested: not the file's host,
    not the default host, and not a token.
    """

    _set(monkeypatch, ENV_MODES[mode])
    project = write_project(tmp_path / "proj")
    extra = f"  token_scope: {PROD_TOKEN_SCOPE}\n" if mode == "entra" else ""
    _hostile(Path(project), extra=extra)
    with pytest.raises(ConfigurationError, match=r"api.url is refused"):
        await publish(project)
    _assert_nothing_reached(api)
    assert not [r for r in api.requests if r.url.host == TOKEN_HOST]


@pytest.mark.parametrize("mode", sorted(ENV_MODES))
async def test_control_the_harness_sees_where_the_credential_goes(api, project, monkeypatch, mode):
    """The control of the test above: with no hostile file, the same environment
    produces a request to the default host carrying the credential. A recorder that
    missed requests would fail here, so its silence above means something."""
    _set(monkeypatch, ENV_MODES[mode])
    await publish(project)
    (sent,) = api.api_requests
    assert sent.url.host == "api.perceivo.ai"
    if mode == "entra":
        assert sent.headers["authorization"].startswith("Bearer ")
        assert len(api.token_requests) == 1
    else:
        assert sent.headers["X-API-Key"] == API_KEY


def test_control_the_nothing_reached_assertion_can_fail():
    """A planted request must trip the assertion the attack test relies on."""
    capture = Capture()
    capture.handle(httpx.Request("POST", f"{EVIL}/v1/orchestrations/import"))
    with pytest.raises(AssertionError, match=EVIL_HOST):
        _assert_nothing_reached(capture)


# --- the environment and the flag still choose the host ------------------------------


async def test_the_environment_chooses_the_host(api, tmp_path, monkeypatch):

    _set(monkeypatch, ENV_MODES["api_key"])
    monkeypatch.setenv("PERCEIVO_API_URL", "https://operator.example")
    project = write_project(tmp_path / "proj")
    _write(Path(project), "api:\n  signing_audience: aud\n")  # non-URL keys still load
    await publish(project)
    (sent,) = api.api_requests
    assert str(sent.url) == "https://operator.example/v1/orchestrations/import"
    assert sent.headers["X-API-Key"] == API_KEY
    # Control: without the variable the same call goes to the default host.
    monkeypatch.delenv("PERCEIVO_API_URL")
    await publish(project)
    assert api.api_requests[-1].url.host == "api.perceivo.ai"


async def test_the_argument_chooses_the_host_over_the_environment(api, project, monkeypatch):
    _set(monkeypatch, ENV_MODES["api_key"])
    monkeypatch.setenv("PERCEIVO_API_URL", "https://env.example")
    await publish(project, base_url="https://arg.example")
    assert [r.url.host for r in api.api_requests] == ["arg.example"]
    # Control: without the argument the environment's host is used.
    await publish(project)
    assert api.api_requests[-1].url.host == "env.example"


def test_the_cli_flag_chooses_the_host(api, project, monkeypatch):
    _set(monkeypatch, ENV_MODES["api_key"])
    monkeypatch.setenv("PERCEIVO_API_URL", "https://env.example")
    result = CliRunner().invoke(
        perceivo_cli, ["publish", project, "--base-url", "https://flag.example"]
    )
    assert result.exit_code == 0, result.output
    assert [r.url.host for r in api.api_requests] == ["flag.example"]
    # Control: without the flag the environment's host is used.
    result = CliRunner().invoke(perceivo_cli, ["publish", project])
    assert result.exit_code == 0, result.output
    assert api.api_requests[-1].url.host == "env.example"


def test_the_cli_reports_a_file_url_refusal_without_a_traceback(api, tmp_path, monkeypatch):

    _set(monkeypatch, ENV_MODES["api_key"])
    project = write_project(tmp_path / "proj")
    path = _hostile(Path(project))
    result = CliRunner().invoke(
        perceivo_cli, ["publish", project, "--base-url", "https://flag.example"]
    )
    assert result.exit_code == 1, result.output
    assert "api.url is refused" in result.output
    assert str(path) in result.output.replace("\n", "")
    assert "Traceback" not in result.output
    _assert_nothing_reached(api)
