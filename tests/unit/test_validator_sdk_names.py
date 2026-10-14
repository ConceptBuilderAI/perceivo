"""``validate orchestration`` reports SDK names a component cannot load."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from perceivo._cli.new_cmd import new as new_cmd
from perceivo.runner.loader import load_component_class
from perceivo.validator import validate_orchestration
from perceivo.validator._sdk_names import _error_text


def _scaffold(tmp_path: Path) -> Path:
    result = CliRunner().invoke(new_cmd, ["orchestration", "hello", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path / "hello"


def _import_errors(project: Path) -> list[str]:
    report = validate_orchestration(str(project))
    return [c.message for c in report.checks if "VAL-IMPORT" in c.id and c.severity == "error"]


def _replace_in_start(project: Path, old: str, new: str) -> Path:
    script = project / "start.py"
    source = script.read_text(encoding="utf-8")
    assert old in source
    script.write_text(source.replace(old, new, 1), encoding="utf-8")
    return script


def test_scaffold_still_passes(tmp_path):
    project = _scaffold(tmp_path)
    assert _import_errors(project) == []
    assert validate_orchestration(str(project)).valid is True


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "input: perceivo.primitives.StartInput",
            "input: perceivo.StartInput",
            "module 'perceivo' has no attribute 'StartInput'",
        ),
        (
            "import perceivo\n",
            "import perceivo\nfrom perceivo import StartInput\n",
            "cannot import name 'StartInput' from 'perceivo'",
        ),
        (
            "import perceivo\n",
            "import perceivo\nimport perceivo.not_a_module\n",
            "No module named 'perceivo.not_a_module'",
        ),
        (
            "import perceivo\n",
            "import perceivo\nimport perceivo.primitives as p\n\nGONE = p.Gone\n",
            "module 'perceivo.primitives' has no attribute 'Gone'",
        ),
    ],
)
def test_component_that_cannot_load_is_an_error(tmp_path, old, new, expected):
    project = _scaffold(tmp_path)
    script = _replace_in_start(project, old, new)

    with pytest.raises(RuntimeError, match=expected):
        load_component_class(str(script))

    errors = _import_errors(project)
    assert any(expected in message for message in errors), errors
    assert validate_orchestration(str(project)).valid is False


@pytest.mark.parametrize(
    "addition",
    [
        "try:\n    from perceivo import Missing\nexcept ImportError:\n    Missing = None\n",
        "try:\n    GONE = perceivo.Gone\nexcept (AttributeError, KeyError):\n    GONE = None\n",
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from perceivo.gone import X\n",
        "GONE = perceivo.Gone if hasattr(perceivo, 'Gone') else None\n",
        "if hasattr(perceivo, 'Gone'):\n    GONE = perceivo.Gone\n",
        "if hasattr(perceivo, 'Gone') and perceivo.Gone:\n    GONE = True\n",
        "GONE = hasattr(perceivo, 'Gone') and perceivo.Gone\n",
        "import contextlib\n\nwith contextlib.suppress(ImportError):\n"
        "    from perceivo import Gone\n",
        "from contextlib import suppress\n\nwith suppress(AttributeError):\n"
        "    GONE = perceivo.Gone\n",
    ],
)
def test_guarded_names_are_not_judged(tmp_path, addition):
    project = _scaffold(tmp_path)
    script = _replace_in_start(project, "import perceivo\n", "import perceivo\n\n" + addition)
    load_component_class(str(script))
    assert _import_errors(project) == []
    assert validate_orchestration(str(project)).valid is True


def test_annotation_only_name_under_future_annotations_is_not_judged(tmp_path):
    project = _scaffold(tmp_path)
    script = _replace_in_start(
        project, "import perceivo\n", "from __future__ import annotations\n\nimport perceivo\n"
    )
    _replace_in_start(project, "input: perceivo.primitives.StartInput", "input: perceivo.Gone")
    load_component_class(str(script))
    assert _import_errors(project) == []


def test_validation_never_executes_the_component(tmp_path):
    project = _scaffold(tmp_path)
    marker = tmp_path / "executed.txt"
    _replace_in_start(
        project,
        "import perceivo\n",
        f"import pathlib\n\nimport perceivo\n\npathlib.Path({str(marker)!r}).write_text('x')\n",
    )
    validate_orchestration(str(project))
    assert not marker.exists()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "cannot import name 'X' from 'perceivo' (/site-packages/perceivo/__init__.py)",
            "cannot import name 'X' from 'perceivo'",
        ),
        (
            "cannot import name 'X' from 'perceivo' (C:\\venv\\Lib\\perceivo\\__init__.py)",
            "cannot import name 'X' from 'perceivo'",
        ),
        (
            "cannot import name 'X' from 'perceivo' (unknown location)",
            "cannot import name 'X' from 'perceivo' (unknown location)",
        ),
        ("bad value (see docs)", "bad value (see docs)"),
        ("bad value (a/b)", "bad value (a/b)"),
    ],
)
def test_error_text_drops_only_the_install_path(message, expected):
    assert _error_text(ImportError(message)) == f"ImportError: {expected}"
