"""Import portable platform bundles as runnable local SDK projects."""

from __future__ import annotations

import json
from pathlib import Path

import click


@click.command("import-bundle")
@click.argument("bundle", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "output_dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory in which to create the local project / component.",
)
@click.option(
    "--name",
    "name_override",
    help="For a component, the file name to write, used exactly (no extension); "
    "for an orchestration, the project name.",
)
@click.option(
    "--members",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Imported capability project whose members '@member:<name>' nodes nest.",
)
def import_bundle(
    bundle: Path, output_dir: Path, name_override: str | None, members: Path | None
) -> None:
    """Convert a platform BUNDLE (component, orchestration or capability) into local files."""
    from ..component import load_bundle as import_component
    from ..orchestration import load_bundle as import_orchestration

    try:
        data = json.loads(bundle.read_text(encoding="utf-8"))
        if data.get("export_type") == "component":
            out = import_component(data, str(output_dir), name_override=name_override)
        else:
            out = import_orchestration(
                data, str(output_dir), name_override=name_override, members=members
            )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _warn_about_declared_packages(data)
    click.echo(str(out))


def _warn_about_declared_packages(data: dict) -> None:
    """Warn about ``additional_packages`` this environment cannot satisfy.

    Component bundles carry the list on the single component; orchestration
    bundles carry it on the metadata and on every component definition, nested
    ones included.
    """
    from .._internal.packages import missing_packages, missing_packages_warning
    from ..orchestration._builder._bundle_integrity import collect_additional_packages

    if data.get("export_type") == "component":
        declared = (data.get("component") or {}).get("additional_packages") or []
    elif data.get("export_type") == "capability_bundle":
        declared = sorted(
            {
                package
                for member in data.get("members") or []
                if isinstance(member, dict)
                for package in collect_additional_packages(member)
            }
        )
    else:
        declared = collect_additional_packages(data)
    missing = missing_packages(declared)
    if missing:
        click.echo(
            "WARNING: " + missing_packages_warning(missing, where="This bundle"),
            err=True,
        )
