"""Public authoring workflows that cross the CLI and runtime boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

import perceivo
from perceivo._cli import main as perceivo_cli
from perceivo.config import MOCKABLE_SERVICES, MocksConfig
from perceivo.local.mocks import load_seed


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(perceivo_cli, list(args))


def _seed_document(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert isinstance(document.get("mocks"), dict)
    assert isinstance(document["mocks"].get("seed"), dict)
    return document


def test_mock_init_discovers_references_and_reports_exact_seed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    components = project / "components" / "nested"
    components.mkdir(parents=True)
    (components / "agent.py").write_text(
        """\
import perceivo

async def run():
    await perceivo.managed_service.ai.invoke("chat", messages=[])
    await perceivo.files.read("notes.txt")
    return await perceivo.secrets.get("api_key")
""",
        encoding="utf-8",
    )
    # Only Python source is part of reference discovery.
    (project / "notes.txt").write_text("perceivo.cluster.redis.connect", encoding="utf-8")
    output = tmp_path / "perceivo.mock.yaml"

    result = _invoke("mock", "init", str(project), "--output", str(output))

    assert result.exit_code == 0, result.output
    document = _seed_document(output)
    assert set(document["mocks"]["seed"]) == {"files", "llm", "secrets"}
    assert "mock stubs for: files, llm, secrets" in result.output


def test_mock_init_without_references_scaffolds_every_public_mock(tmp_path: Path) -> None:
    project = tmp_path / "empty-project"
    project.mkdir()
    output = tmp_path / "perceivo.mock.yaml"

    result = _invoke("mock", "init", str(project), "--output", str(output))

    assert result.exit_code == 0, result.output
    document = _seed_document(output)
    assert document["mocks"]["enabled"] is True
    assert document["mocks"]["default_mode"] == "mock"
    assert set(document["mocks"]["seed"]) == set(MOCKABLE_SERVICES)
    assert f"mock stubs for: {', '.join(sorted(MOCKABLE_SERVICES))}" in result.output


def test_mock_init_refuses_existing_file_then_force_replaces_it(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "perceivo.mock.yaml"
    original = "owner-authored: keep-me\n"
    output.write_text(original, encoding="utf-8")

    refused = _invoke("mock", "init", str(project), "--output", str(output))

    assert refused.exit_code != 0
    assert "already exists" in refused.output
    assert "--force" in refused.output
    assert output.read_text(encoding="utf-8") == original

    replaced = _invoke("mock", "init", str(project), "--output", str(output), "--force")

    assert replaced.exit_code == 0, replaced.output
    assert "owner-authored" not in output.read_text(encoding="utf-8")
    assert set(_seed_document(output)["mocks"]["seed"]) == set(MOCKABLE_SERVICES)


def test_mock_add_round_trips_nested_json_through_runtime_seed_loader(
    tmp_path: Path,
) -> None:
    seed_file = tmp_path / "perceivo.mock.yaml"
    nested = {
        "result": {
            "items": [1, {"id": "two", "labels": ["a", "b"]}],
            "page": {"number": 3, "complete": True},
        }
    }

    result = _invoke(
        "mock",
        "add",
        "mcp",
        "search_docs",
        "--value",
        json.dumps(nested),
        "--file",
        str(seed_file),
    )

    assert result.exit_code == 0, result.output
    document = _seed_document(seed_file)
    assert document["mocks"]["seed"]["mcp"]["calls"]["search_docs"] == nested

    loaded = load_seed(MocksConfig(seed_file=str(seed_file)))
    assert loaded["mcp"]["calls"]["search_docs"] == nested
    assert loaded["llm"]["completion"]


def test_mock_add_keeps_plain_strings_and_prior_entries(tmp_path: Path) -> None:
    seed_file = tmp_path / "perceivo.mock.yaml"
    first = _invoke(
        "mock",
        "add",
        "secrets",
        "TOKEN_ALIAS",
        "--value",
        "plain text that is not JSON",
        "--file",
        str(seed_file),
    )
    second = _invoke(
        "mock",
        "add",
        "files",
        "documents/example.txt",
        "--value",
        "line one: line two",
        "--file",
        str(seed_file),
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    seed = _seed_document(seed_file)["mocks"]["seed"]
    assert seed["secrets"]["values"]["TOKEN_ALIAS"] == "plain text that is not JSON"
    assert seed["files"]["documents/example.txt"] == "line one: line two"


def test_mock_add_unknown_service_lists_choices_without_creating_file(
    tmp_path: Path,
) -> None:
    seed_file = tmp_path / "perceivo.mock.yaml"

    result = _invoke("mock", "add", "unknown", "key", "--file", str(seed_file))

    assert result.exit_code != 0
    assert "Unknown service 'unknown'" in result.output
    for service in MOCKABLE_SERVICES:
        assert service in result.output
    assert not seed_file.exists()


def test_template_error_lists_the_public_catalog() -> None:
    catalog = perceivo.templates.list()
    names = {item.name for item in catalog}
    assert names
    assert all(item.description.strip() for item in catalog)

    with pytest.raises(perceivo.types.NotFoundError) as raised:
        perceivo.templates.use("unknown-template")

    message = str(raised.value)
    assert "unknown-template" in message
    for name in names:
        assert name in message
