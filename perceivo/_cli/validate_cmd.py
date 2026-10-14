"""perceivo validate: validate components and orchestrations."""

import json

import click


@click.group()
def validate():
    """Validate components and orchestrations."""
    pass


@validate.command()
@click.argument("script_path")
@click.argument("manifest_path", required=False)  # defaults to <script>.yaml, as export does
@click.option(
    "--type",
    "component_type",
    default=None,
    help="Component type (read from the manifest, else inferred from the script).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable JSON report.")
def component(
    script_path: str, manifest_path: str | None, component_type: str | None, as_json: bool
):
    """Validate a single component script and manifest."""
    from pathlib import Path

    from ..validator._component import validate_source
    from ..validator._component_type import ComponentTypeError, resolve_component_type
    from ..validator._paths import NOT_RECORDED, read_text_or_reason, recorded_ports
    from ..validator._report import ValidationReport
    from ..validator._unreadable_files import unreadable_manifest_check

    script, script_unreadable = read_text_or_reason(Path(script_path))
    # The manifest export reads, so the two judge the same files.
    manifest_file = (
        Path(manifest_path) if manifest_path else Path(script_path).resolve().with_suffix(".yaml")
    )
    manifest_yaml, unreadable = (
        read_text_or_reason(manifest_file) if manifest_file.exists() else (None, None)
    )

    if not script_unreadable:
        try:
            component_type = resolve_component_type(script_path, component_type, manifest_yaml)
        except ComponentTypeError as exc:
            if unreadable is None:
                raise click.ClickException(str(exc)) from exc
            # The type was looked up in a manifest that cannot be read, so that is the
            # one finding, in export's words.
            _emit(
                ValidationReport(valid=False, checks=[unreadable_manifest_check(unreadable)]),
                as_json,
            )
            raise SystemExit(1) from exc

    rows = recorded_ports(Path(script_path).resolve(), manifest_yaml)
    recorded = rows is not NOT_RECORDED
    report = validate_source(
        script=script or "",
        component_type=component_type,
        manifest_yaml=manifest_yaml,
        ports=rows if recorded else None,
        ports_recorded=recorded,
        script_unreadable=script_unreadable,
        manifest_unreadable=unreadable,
    )

    _emit(report, as_json)
    if not report.valid:
        raise SystemExit(1)


@validate.command()
@click.argument("project_dir", default=".")
@click.option("--no-scripts", is_flag=True, help="Skip individual component script validation.")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable JSON report.")
def orchestration(project_dir: str, no_scripts: bool, as_json: bool):
    """Validate an orchestration project directory."""
    from ..validator import validate_orchestration as _validate

    report = _validate(project_dir, validate_scripts=not no_scripts)

    _emit(report, as_json)
    if not report.valid:
        raise SystemExit(1)


def _emit(report, as_json: bool) -> None:
    """Emit a validation report as JSON or a rich human-readable summary."""
    if as_json:
        payload = report.to_dict()
        payload["status"] = "PASS" if report.valid else "FAIL"
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    click.echo(report.format())
    click.echo("")
    click.echo(f"RESULT: {'PASS' if report.valid else 'FAIL'}")
