"""perceivo export: write a local component, orchestration or capability as a bundle."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ._refusals import refusals_as_errors


def _detect_and_build(source: Path) -> tuple[str, dict]:
    """Return (kind, bundle) for a component ``.py``, orchestration or capability dir."""
    from .._bundle.capability import is_capability_project, project_to_capability
    from .._bundle.component import component_to_bundle
    from .._bundle.orchestration import orchestration_to_bundle

    if source.is_file() and source.suffix == ".py":
        return "component", component_to_bundle(source)
    if source.is_dir():
        if is_capability_project(source):
            return "capability", project_to_capability(source)
        if (source / "orchestration.yaml").exists():
            return "orchestration", orchestration_to_bundle(source)
        scripts = list(source.glob("*.py"))
        if len(scripts) == 1:
            return "component", component_to_bundle(scripts[0])
        raise click.ClickException(
            f"{source} is neither an orchestration project (no orchestration.yaml) "
            f"nor a single-component directory ({len(scripts)} .py files found)."
        )
    raise click.ClickException(
        f"Cannot export {source}: expected a component .py or a project dir."
    )


@click.command("export")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Bundle file to write (default: <name>.bundle.json in the CWD).",
)
def export(source: Path, output: Path | None) -> None:
    """Export a component (.py) or orchestration project into a platform bundle JSON."""
    with refusals_as_errors():
        kind, bundle = _detect_and_build(source.resolve())
    if output is None:
        if kind == "component":
            stem = bundle["component"]["name"]
        elif kind == "capability":
            stem = (bundle.get("capability") or {}).get("name") or "capability"
        else:
            stem = (bundle.get("orchestration") or {}).get("name") or "orchestration"
        output = Path(f"{stem}.bundle.json")
    if ".." in output.parts:
        raise click.ClickException(f"Output path must not contain '..': {output}")
    with refusals_as_errors():
        output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    click.echo(str(output.resolve()))
