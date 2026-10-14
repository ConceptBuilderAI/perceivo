"""The file names a component import writes are ordinary files inside the target, on every OS."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from perceivo._bundle._file_names import resolve_within, safe_stem
from perceivo._bundle.component import bundle_to_component, component_to_bundle
from perceivo._cli import main

RESERVED = [
    "CON",
    "con",
    "Prn",
    "aux",
    "NUL",
    "nul.txt",
    "con .txt",
    "CON.",
    "CONIN$",
    "conout$",
    "COM0",
    "com9",
    "LPT1",
    "lpt9",
    "COM¹",
    "LPT³",
]


def _bundle(tmp_path: Path, display_name: str) -> dict:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "greet.py").write_text("# greet\n", encoding="utf-8")
    manifest = {"extension_type": "TOOL", "name": "greet", "input_ports": [], "output_ports": []}
    (source / "greet.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    bundle = component_to_bundle(source / "greet.py")
    bundle["component"]["name"] = display_name
    return bundle


@pytest.mark.parametrize("name", RESERVED)
def test_an_override_naming_a_device_is_refused_before_writing(tmp_path, name):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="reserved device name"):
        bundle_to_component(_bundle(tmp_path, "Fine"), out, name_override=name)
    assert not out.exists()


def test_the_cli_refuses_a_device_name(tmp_path):
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_bundle(tmp_path, "Fine")), encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main, ["import-bundle", str(bundle), "--output", str(out), "--name", "CON"]
    )
    assert result.exit_code == 1
    assert "reserved device name" in result.output
    assert not out.exists()


@pytest.mark.parametrize("name", ["console", "COM10", "lpt", "con_", "nul_txt", "Aux Wait"])
def test_names_that_only_resemble_a_device_are_kept(name):
    assert safe_stem(name) == name


@pytest.mark.parametrize(
    ("display_name", "stem"),
    [
        ("CON", "con_"),
        ("Nul", "nul_"),
        ("COM1", "com1_"),
        ("lpt 9", "lpt_9"),
        ("con.txt", "con_txt"),
    ],
)
def test_a_display_name_slugged_to_a_device_still_imports(tmp_path, display_name, stem):
    out = tmp_path / "out"
    bundle_to_component(_bundle(tmp_path, display_name), out)
    assert sorted(p.name for p in out.iterdir()) == [
        f"{stem}.component.json",
        f"{stem}.py",
        f"{stem}.yaml",
    ]


@pytest.mark.parametrize(
    "name",
    ["ab:c", "a\x1bb", "a\tb", "a\x7fb", "a‮b", "a‎b", "a‏b", "a⁦b", "a⁩b", "a؜b"],
)
def test_an_override_with_a_stream_or_hidden_character_is_refused(tmp_path, name):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="Unsafe component name"):
        bundle_to_component(_bundle(tmp_path, "Fine"), out, name_override=name)
    assert not out.exists()


def test_resolve_within_refuses_a_name_that_leads_out(tmp_path):
    with pytest.raises(ValueError, match="Refusing to write outside"):
        resolve_within(tmp_path / "out", "../escaped.py")


def _link_directory(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        # A junction needs no privilege, unlike a Windows symlink.
        _winapi.CreateJunction(str(target), str(link))


def test_an_import_does_not_follow_a_link_out_of_the_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    try:
        _link_directory(out / "foo.py", outside)
    except (OSError, ImportError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")
    with pytest.raises(ValueError, match="Refusing to write outside"):
        bundle_to_component(_bundle(tmp_path, "Fine"), out, name_override="foo")
    assert list(outside.iterdir()) == []


def test_a_record_nested_too_deep_to_read_is_refused_cleanly(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "foo.py").write_text("# kept\n", encoding="utf-8")
    depth = 200_000
    (out / "foo.component.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    bundle = tmp_path / "b.json"
    bundle.write_text(json.dumps(_bundle(tmp_path, "Fine")), encoding="utf-8")
    result = CliRunner().invoke(
        main, ["import-bundle", str(bundle), "--output", str(out), "--name", "foo"]
    )
    assert result.exit_code == 1
    assert "no import record" in result.output
    assert not isinstance(result.exception, RecursionError)
    assert (out / "foo.py").read_text(encoding="utf-8") == "# kept\n"
