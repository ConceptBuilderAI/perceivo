"""Which ``perceivo.yaml`` a public-API call reads, and the ``api:`` section.

The file is found from the **project**, walking upward, never from the working
directory: ``perceivo publish proj`` run from a parent whose own ``perceivo.yaml``
points somewhere else must still read ``proj``'s.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perceivo import config as config_mod
from perceivo._internal.auth import resolve
from perceivo._internal.config_discovery import (
    ApiConfig,
    build_api_config,
    discover_config,
    read_api_config,
)
from perceivo.types import ConfigurationError

pytestmark = pytest.mark.usefixtures("no_network", "clean_credentials")

KEY_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _yaml(directory: Path, audience: str) -> Path:
    """A ``perceivo.yaml`` whose ``api.signing_audience`` marks which file was read."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "perceivo.yaml"
    path.write_text(f"api:\n  signing_audience: {audience}\n", encoding="utf-8")
    return path


def _audience(project_dir: str | Path) -> str | None:
    return resolve(project_dir=project_dir).credentials.audience


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """``a/perceivo.yaml`` (audience X) as the cwd, and ``a/proj`` beneath it."""
    a = tmp_path / "a"
    _yaml(a, "aud-x")
    (a / "proj").mkdir()
    monkeypatch.chdir(a)
    monkeypatch.setenv("PERCEIVO_API_KEY", "k")
    monkeypatch.setenv("PERCEIVO_KEY_ID", KEY_ID)
    monkeypatch.setenv("PERCEIVO_SIGNING_SECRET", "ab" * 32)
    return a


def test_the_projects_own_file_wins_over_the_cwds(tree):
    _yaml(tree / "proj", "aud-y")
    assert _audience("proj") == "aud-y"


def test_without_its_own_file_the_upward_walk_finds_the_parents(tree):
    assert _audience("proj") == "aud-x"


def test_a_project_elsewhere_does_not_read_the_cwds_file(tree, tmp_path):
    elsewhere = tmp_path / "b" / "proj"
    elsewhere.mkdir(parents=True)
    assert discover_config(elsewhere) is None
    assert _audience(elsewhere) == "perceivo-api-prod"


def test_perceivo_config_names_the_file_outright(tree, tmp_path, monkeypatch):
    _yaml(tree / "proj", "aud-y")
    named = _yaml(tmp_path / "named", "aud-z")
    monkeypatch.setenv("PERCEIVO_CONFIG", str(named))
    assert _audience("proj") == "aud-z"


def test_perceivo_config_naming_a_missing_file_is_refused(tree, monkeypatch):
    monkeypatch.setenv("PERCEIVO_CONFIG", str(tree / "nope.yaml"))
    with pytest.raises(ConfigurationError, match=r"nope.yaml"):
        discover_config("proj")


def test_discovery_accepts_a_file_path_and_defaults_to_the_cwd(tree):
    script = tree / "proj" / "x.py"
    script.write_text("", encoding="utf-8")
    assert discover_config(script) == tree / "perceivo.yaml"
    assert discover_config() == (tree / "perceivo.yaml").resolve()


# --- the api: section ----------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "variable"),
    [
        ("api_key", "PERCEIVO_API_KEY"),
        ("signing_secret", "PERCEIVO_SIGNING_SECRET"),
        ("client_secret", "PERCEIVO_CLIENT_SECRET"),
    ],
)
def test_a_secret_in_perceivo_yaml_is_refused_by_name(tmp_path, key, variable):
    path = tmp_path / "perceivo.yaml"
    path.write_text(f"api:\n  {key}: s3cret-value\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as err:
        read_api_config(path)
    assert f"api.{key}" in str(err.value)
    assert variable in str(err.value)
    assert "s3cret-value" not in str(err.value)
    # ...and perceivo.config.load() refuses the same file.
    with pytest.raises(ConfigurationError, match=f"api.{key}"):
        config_mod.load(str(path))


def test_a_secret_in_perceivo_yaml_is_refused_through_resolve(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "perceivo.yaml").write_text("api:\n  api_key: k\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"api.api_key"):
        resolve(project_dir=project)


@pytest.mark.parametrize(
    ("section", "match"),
    [
        (["not", "a", "mapping"], "must be a mapping"),
        ({"tenant": "t"}, "unknown api key"),
        ({"key_id": 7}, "non-empty string"),
        ({"key_id": "  "}, "non-empty string"),
    ],
)
def test_a_malformed_api_section_is_refused(section, match):
    with pytest.raises(ConfigurationError, match=match):
        build_api_config(section)


def test_a_non_mapping_file_is_refused(tmp_path):
    path = tmp_path / "perceivo.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not a mapping"):
        read_api_config(path)


def test_config_load_exposes_the_api_section(tmp_path):
    path = tmp_path / "perceivo.yaml"
    path.write_text("api:\n  key_id: kid\n  federated_token_file: t\n", encoding="utf-8")
    loaded = config_mod.load(str(path))
    assert loaded.api.key_id == "kid"
    assert loaded.api.federated_token_file == str((tmp_path / "t").resolve())
    assert config_mod.load(str(tmp_path / "absent.yaml")).api == ApiConfig()
