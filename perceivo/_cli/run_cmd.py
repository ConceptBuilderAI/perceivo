"""perceivo run: run components and orchestrations locally against mocks."""

import asyncio
import json

import click

_UTF8 = "utf-8"


@click.group()
def run():
    """Run components and orchestrations locally."""
    pass


@run.command()
@click.argument("script_path")
@click.option(
    "--type",
    "component_type",
    default=None,
    help="Component type (read from the manifest, else inferred from the script).",
)
@click.option("--manifest", "manifest_path", default=None, help="Path to manifest YAML.")
@click.option("--input", "input_json", default=None, help="Input data as JSON string.")
@click.option("--input-file", default=None, help="Path to JSON file with input data.")
@click.option("--config", "config_path", default=None, help="Path to perceivo.yaml.")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable JSON result.")
def component(
    script_path: str,
    component_type: str | None,
    manifest_path: str | None,
    input_json: str | None,
    input_file: str | None,
    config_path: str | None,
    as_json: bool,
):
    """Run a single component."""
    from pathlib import Path

    from ..runner import run_component as _run_component

    input_data = {}
    if input_json:
        input_data = json.loads(input_json)
    elif input_file:
        input_data = json.loads(Path(input_file).read_text(encoding=_UTF8))

    if not manifest_path:
        p = Path(script_path)
        candidate = p.with_suffix(".yaml")
        if candidate.exists():
            manifest_path = str(candidate)

    from ..validator._component_type import ComponentTypeError, resolve_component_type
    from ..validator._paths import read_text_or_reason

    # A script export refuses is refused here in its words, not run or crashed on;
    # a missing one is left to the runner's own report.
    if Path(script_path).exists():
        _, unreadable = read_text_or_reason(Path(script_path), script_path)
        if unreadable is not None:
            raise click.ClickException(unreadable)

    manifest_yaml = None
    if manifest_path:
        manifest_yaml, unreadable = read_text_or_reason(Path(manifest_path), manifest_path)
        if unreadable is not None:
            raise click.ClickException(unreadable)
    try:
        component_type = resolve_component_type(script_path, component_type, manifest_yaml)
    except ComponentTypeError as exc:
        raise click.ClickException(str(exc)) from exc

    result = asyncio.run(
        _run_component(
            script_path=script_path,
            component_type=component_type,
            input_data=input_data,
            manifest_path=manifest_path,
            config_path=config_path,
        )
    )

    _emit(result, as_json)
    if not result.success:
        raise SystemExit(1)


@run.command()
@click.argument("project_dir", default=".")
@click.option("--input", "input_json", default=None, help="Input data as JSON string.")
@click.option("--input-file", default=None, help="Path to JSON file with input data.")
@click.option("--config", "config_path", default=None, help="Path to perceivo.yaml.")
@click.option("--mocks", "mocks_file", default=None, help="Path to a perceivo.mock.yaml seed file.")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable JSON result.")
def orchestration(
    project_dir: str,
    input_json: str | None,
    input_file: str | None,
    config_path: str | None,
    mocks_file: str | None,
    as_json: bool,
):
    """Run an orchestration from a project directory."""
    import os
    from pathlib import Path

    from ..runner import run_orchestration as _run_orchestration
    from ..runner._nested import UnreadableChildError
    from ..validator._orchestration._loader import orchestration_unreadable
    from ..validator._orchestration._nested import nested_cycle

    unreadable = orchestration_unreadable(project_dir)
    if unreadable is not None:
        raise click.ClickException(unreadable)
    # A project that nests itself would recurse until the interpreter gives up.
    cycle = nested_cycle(project_dir)
    if cycle is not None:
        raise click.ClickException(cycle)

    if mocks_file:
        os.environ["PERCEIVO_MOCKS_SEED_FILE"] = mocks_file

    input_data = {}
    if input_json:
        input_data = json.loads(input_json)
    elif input_file:
        input_data = json.loads(Path(input_file).read_text(encoding=_UTF8))

    try:
        result = asyncio.run(
            _run_orchestration(
                project_dir=project_dir,
                input_data=input_data,
                config_path=config_path,
            )
        )
    except UnreadableChildError as exc:
        raise click.ClickException(str(exc)) from None

    _emit(result, as_json)
    if not result.success:
        raise SystemExit(1)


def _result_dict(result) -> dict:
    """Serialize an ExecutionResult to a JSON-friendly dict."""
    artifact = None
    if result.artifact is not None:
        artifact = {"type": getattr(result.artifact.type, "value", str(result.artifact.type))}
        data = getattr(result.artifact, "data", None)
        if isinstance(data, (dict, list, str, int, float, bool)) or data is None:
            artifact["data"] = data
    return {
        "success": result.success,
        "status": "PASS" if result.success else "FAIL",
        "error": result.error,
        "artifact": artifact,
        "response_data": result.response_data,
        "trace": [t.to_dict() if hasattr(t, "to_dict") else t for t in result.trace],
        "mock_calls": result.mock_calls,
        "mock_call_count": _mocked(result.mock_calls),
        "real_call_count": len(result.mock_calls) - _mocked(result.mock_calls),
        "missing_packages": list(getattr(result, "missing_packages", []) or []),
    }


def _mocked(calls) -> int:
    """How many recorded platform calls were served by a mock."""
    return sum(1 for call in calls if call.get("mode") == "mock")


def _emit(result, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(_result_dict(result), indent=2, default=str))
    else:
        _print_human(result)


def _print_call(call: dict, indent: str) -> None:
    click.echo(
        f"{indent}mock: {call['service']}.{call['operation']} "
        f"[{call['mode']}] {call.get('summary', '')}".rstrip()
    )


def _print_trace(result) -> None:
    """Print the execution trace with the mocks that served each platform call.

    An orchestration run reports per node. A component run has no nodes, since the
    component is the whole run, so its calls print as one flat list rather
    than going unreported.
    """
    if not result.trace:
        if result.mock_calls:
            click.echo("Execution trace:")
            for call in result.mock_calls:
                _print_call(call, "  ")
            click.echo("")
        return
    click.echo("Execution trace:")
    for entry in result.trace:
        item = entry.to_dict() if hasattr(entry, "to_dict") else entry
        marker = {"ok": "+", "failed": "x", "skipped": "-"}.get(item["status"], "?")
        click.echo(f"  [{marker}] {item['node']} ({item['component_type']})")
        for call in item.get("mocks", []):
            _print_call(call, "        ")
        if item.get("error"):
            click.echo(f"        error: {item['error']}")
    click.echo("")


def _print_artifact_and_data(result) -> None:
    if result.artifact is not None:
        click.echo(f"\nArtifact type: {result.artifact.type}")
        data = result.artifact.data
        if isinstance(data, (dict, list)):
            click.echo(f"Artifact data:\n{json.dumps(data, indent=2, default=str)}")
        elif data is not None:
            click.echo(f"Artifact data: {data}")
    if result.response_data:
        click.echo(f"\nResponse data:\n{json.dumps(result.response_data, indent=2, default=str)}")


def _print_missing_packages(result) -> None:
    """Say which declared additional_packages this interpreter does not have."""
    from .._internal.packages import missing_packages_warning

    missing = list(getattr(result, "missing_packages", []) or [])
    if missing:
        click.echo(
            "WARNING: " + missing_packages_warning(missing, where="This orchestration"),
            err=True,
        )


def _print_human(result) -> None:
    """Print a human-readable run result with a per-node execution trace."""
    _print_missing_packages(result)
    _print_trace(result)
    if result.success:
        mocked = _mocked(result.mock_calls)
        real = len(result.mock_calls) - mocked
        served = f"{mocked} mock call(s) served"
        if real:
            served += f", {real} real call(s) made"
        click.echo(f"RESULT: PASS  ({served})")
        _print_artifact_and_data(result)
    else:
        click.echo(f"RESULT: FAIL\nReason: {result.error}")
