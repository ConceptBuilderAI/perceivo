"""``perceivo new``: adapter and review components, ``--template``, one ``.env``.

* ``new adapter`` and ``new review`` scaffold files that ``validate component``
  passes and ``run component`` runs (the review through today's local
  auto-approval).
* ``new orchestration h --template <name>`` writes exactly what
  ``perceivo.templates.use(<name>, into=..., as_name="h")`` writes, and an
  unknown name is refused with the catalog listed.
* ``init`` then ``new orchestration`` leaves one ``perceivo.yaml`` and one
  ``.env`` in the tree; a scaffold in an empty directory still writes both.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import perceivo.templates
from perceivo._cli import main as perceivo_cli

CONFIG = "perceivo.yaml"
ENV = ".env"
DIR_FLAG = "--dir"
PROJECT = "hello"
COMPONENT = "component"
ORCHESTRATION = "orchestration"
TEMPLATE_FLAG = "--template"
REVIEW = "review"
UTF8 = "utf-8"


def _invoke(args: list[str]):
    return CliRunner().invoke(perceivo_cli, args)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _named(root: Path, filename: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob(filename))


# --- new adapter / new review ---------------------------------------------------


@pytest.mark.parametrize("kind", ["adapter", REVIEW])
def test_new_component_scaffold_validates(tmp_path, kind):
    scaffolded = _invoke(["new", kind, "thing", DIR_FLAG, str(tmp_path)])
    assert scaffolded.exit_code == 0, scaffolded.output
    script, manifest = str(tmp_path / "thing.py"), str(tmp_path / "thing.yaml")
    result = _invoke(["validate", COMPONENT, script, manifest])
    assert result.exit_code == 0, result.output


def test_new_adapter_scaffold_runs(tmp_path):
    _invoke(["new", "adapter", "shape", DIR_FLAG, str(tmp_path)])
    result = _invoke(
        ["run", COMPONENT, str(tmp_path / "shape.py"), "--input", '{"value": "v"}', "--json"]
    )
    assert result.exit_code == 0, result.output
    assert '"value": "v"' in result.output


def test_new_review_scaffold_runs_and_is_auto_approved(tmp_path):
    _invoke(["new", REVIEW, "check", DIR_FLAG, str(tmp_path)])
    result = _invoke(["run", COMPONENT, str(tmp_path / "check.py"), "--json"])
    assert result.exit_code == 0, result.output
    assert '"approved": true' in result.output


def test_new_review_manifest_declares_review(tmp_path):
    _invoke(["new", REVIEW, "check", DIR_FLAG, str(tmp_path)])
    manifest = (tmp_path / "check.yaml").read_text(encoding=UTF8)
    assert manifest.startswith("extension_type: REVIEW\n")


# The upstream response_data keys the engine's REVIEW handler reads; each is optional.
_REVIEW_HANDLER_KEYS = {
    "survey_spec",
    "review_artifacts",
    "context",
    "assignee_user_sub",
    "assignee_group_id",
}


def test_new_review_manifest_declares_the_keys_the_review_handler_reads(tmp_path):
    _invoke(["new", REVIEW, "check", DIR_FLAG, str(tmp_path)])
    manifest = yaml.safe_load((tmp_path / "check.yaml").read_text(encoding=UTF8))
    (port,) = manifest["input_ports"]
    fields = port["request_data"]
    assert set(fields) == _REVIEW_HANDLER_KEYS
    assert all(field["required"] is False for field in fields.values())


# --- new orchestration --template ---------------------------------------------------


@pytest.mark.parametrize("template", ["rag-ingest", "sequential-executor"])
def test_new_orchestration_template_equals_templates_use(tmp_path, template):
    by_cli, by_api = tmp_path / "cli", tmp_path / "api"
    result = _invoke(
        ["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(by_cli), TEMPLATE_FLAG, template]
    )
    assert result.exit_code == 0, result.output
    perceivo.templates.use(template, into=str(by_api), as_name=PROJECT)
    cli_tree = _tree(by_cli)
    assert cli_tree
    assert cli_tree == _tree(by_api)


def test_new_orchestration_unknown_template_lists_the_catalog(tmp_path):
    result = _invoke(
        ["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(tmp_path), TEMPLATE_FLAG, "nope"]
    )
    assert result.exit_code == 1
    assert "unknown template 'nope'" in result.output
    for name in ("rag-ingest", "llm-request", "sequential-executor"):
        assert name in result.output


# --- one perceivo.yaml and one .env -------------------------------------------------


def test_init_then_new_orchestration_leaves_one_of_each(tmp_path):
    assert _invoke(["init", DIR_FLAG, str(tmp_path)]).exit_code == 0
    assert _invoke(["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(tmp_path)]).exit_code == 0
    assert _named(tmp_path, CONFIG) == [CONFIG]
    assert _named(tmp_path, ENV) == [ENV]


def test_new_orchestration_in_an_empty_directory_writes_both(tmp_path):
    assert _invoke(["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(tmp_path)]).exit_code == 0
    assert _named(tmp_path, CONFIG) == [f"{PROJECT}/{CONFIG}"]
    assert _named(tmp_path, ENV) == [f"{PROJECT}/{ENV}"]


def test_template_scaffold_under_init_leaves_one_of_each(tmp_path):
    _invoke(["init", DIR_FLAG, str(tmp_path)])
    result = _invoke(
        ["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(tmp_path), TEMPLATE_FLAG, "rag-query"]
    )
    assert result.exit_code == 0, result.output
    assert _named(tmp_path, ENV) == [ENV]


def test_each_file_is_decided_on_its_own(tmp_path):
    # Only a .env above: the project still gets its perceivo.yaml, and no second .env.
    (tmp_path / ENV).write_text("KEY=v\n", encoding=UTF8)
    _invoke(["new", ORCHESTRATION, PROJECT, DIR_FLAG, str(tmp_path)])
    assert _named(tmp_path, CONFIG) == [f"{PROJECT}/{CONFIG}"]
    assert _named(tmp_path, ENV) == [ENV]


@pytest.mark.parametrize("command", [["init"], ["new", ORCHESTRATION, PROJECT]])
def test_scaffolded_config_pins_no_path_outside_the_home(tmp_path, command):
    # A files: section would move working and org files out of the home; the
    # scaffold carries it only as a commented example.
    _invoke([*command, DIR_FLAG, str(tmp_path)])
    written = next(tmp_path.rglob(CONFIG))
    parsed = yaml.safe_load(written.read_text(encoding=UTF8))
    assert "files" not in parsed
    assert "root" not in parsed
