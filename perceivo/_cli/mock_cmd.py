"""perceivo mock: scaffold and edit mock seed data for local runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import yaml

# Substrings that mark a project's use of each platform service. Keep in step
# with ``tests/unit/test_mockable_services_pin.py``.
_SERVICE_MARKERS: dict[str, tuple[str, ...]] = {
    "llm": ("managed_service.ai",),
    "redis": ("cluster.redis", "get_redis"),
    "milvus": ("cluster.milvus", "get_milvus"),
    "elasticsearch": ("cluster.elasticsearch", "get_elasticsearch"),
    "database": (
        "managed_service.database",
        "database_query",
        "resolve_platform_database",
        "external_database_query",
    ),
    "mcp": ("cluster.mcp",),
    "secrets": ("perceivo.secrets", "secrets.get"),
    "files": ("perceivo.files",),
    "chat": ("alerts.send_direct", "send_chat"),
    "inference": ("intelligence.inference", "predict_inference"),
    "capabilities": ("perceivo.capabilities", "list_execution_graphs"),
}


@click.group()
def mock():
    """Scaffold and edit the mock seed data (perceivo.mock.yaml)."""
    pass


def _referenced_services(project_dir: Path) -> list[str]:
    """Return the services a project references, by scanning its Python files."""
    text = ""
    for py in project_dir.rglob("*.py"):
        try:
            text += py.read_text(encoding="utf-8")
        except OSError:
            continue
    found = [
        service
        for service, markers in _SERVICE_MARKERS.items()
        if any(marker in text for marker in markers)
    ]
    return sorted(found)


@mock.command("init")
@click.argument("project_dir", default=".")
@click.option("--output", "output", default="perceivo.mock.yaml", help="Seed file to write.")
@click.option("--force", is_flag=True, help="Overwrite an existing seed file.")
def init(project_dir: str, output: str, force: bool):
    """Scaffold a mock seed file with a stub for every referenced service."""
    from ..local.mocks import scaffold_seed

    root = Path(project_dir)
    out_path = Path(output)
    if out_path.exists() and not force:
        raise click.ClickException(f"{out_path} already exists. Re-run with --force to overwrite.")

    services = _referenced_services(root)
    if not services:
        services = sorted(_SERVICE_MARKERS.keys())

    seed = scaffold_seed(services)
    doc = {"mocks": {"enabled": True, "default_mode": "mock", "seed": seed}}
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    click.echo(f"Wrote {out_path} with mock stubs for: {', '.join(services)}")


@mock.command("add")
@click.argument("service")
@click.argument("key")
@click.option(
    "--value", "value", default=None, help="JSON value (defaults to a placeholder string)."
)
@click.option("--file", "seed_file", default="perceivo.mock.yaml", help="Seed file to edit.")
def add(service: str, key: str, value: str | None, seed_file: str):
    """Add or adjust a single mock entry (e.g. `mock add mcp search`)."""
    if service not in _SERVICE_MARKERS:
        raise click.ClickException(
            f"Unknown service '{service}'. Choose one of: {', '.join(sorted(_SERVICE_MARKERS))}."
        )

    parsed: object
    if value is None:
        parsed = f"mock {service} {key}"
    else:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value

    path = Path(seed_file)
    doc: dict[str, Any] = {}
    if path.exists():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mocks = doc.setdefault("mocks", {})
    seed = mocks.setdefault("seed", {})
    _place(seed, service, key, parsed)

    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    click.echo(f"Set mocks.seed under service '{service}' for key '{key}' in {path}")


def _place(seed: dict, service: str, key: str, value: object) -> None:
    """Place *value* under the seed location conventional for *service*."""
    block = seed.setdefault(service, {})
    if service == "mcp":
        block.setdefault("calls", {})[key] = value
    elif service == "secrets":
        block.setdefault("values", {})[key] = value
    elif service == "files":
        seed.setdefault("files", {})[key] = value
    else:
        block[key] = value
