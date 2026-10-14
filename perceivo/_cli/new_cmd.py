"""perceivo new: scaffold components and orchestrations."""

from pathlib import Path

import click


@click.group()
def new():
    """Scaffold a new component or orchestration."""
    pass


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def agent(name: str, directory: str):
    """Scaffold a new AGENT component."""
    _scaffold_component(name, "agent", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def tool(name: str, directory: str):
    """Scaffold a new TOOL component."""
    _scaffold_component(name, "tool", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def start(name: str, directory: str):
    """Scaffold a new START component."""
    _scaffold_component(name, "start", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def end(name: str, directory: str):
    """Scaffold a new END component."""
    _scaffold_component(name, "end", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def fsm(name: str, directory: str):
    """Scaffold a new FSM component."""
    _scaffold_component(name, "fsm", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def policy(name: str, directory: str):
    """Scaffold a new POLICY component."""
    _scaffold_component(name, "policy", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def adapter(name: str, directory: str):
    """Scaffold a new ADAPTER component."""
    _scaffold_component(name, "adapter", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
def review(name: str, directory: str):
    """Scaffold a new REVIEW component."""
    _scaffold_component(name, "review", directory)


@new.command()
@click.argument("name")
@click.option("--dir", "directory", default=".", help="Output directory.")
@click.option(
    "--template",
    "template",
    default=None,
    help="Scaffold from a catalog template (see perceivo.templates.list()).",
)
def orchestration(name: str, directory: str, template: str | None):
    """Scaffold a new orchestration project."""
    if template:
        path = _from_template(template, name, directory)
    else:
        from ..orchestration import create as _orch_create

        path = _orch_create(name, directory)

    click.echo(f"\nOrchestration '{name}' created at {path}")
    click.echo("  Files:")
    for f in sorted(path.iterdir()):
        click.echo(f"    {f.name}")
    click.echo(f"\n  Run: perceivo run orchestration {path}")


def _from_template(template: str, name: str, directory: str) -> Path:
    """Copy catalog template *template* into *directory* as *name*.

    An unknown name is refused with every catalog name listed.
    """
    from .. import templates as _templates
    from ..types.errors import NotFoundError

    try:
        return _templates.use(template, into=directory, as_name=name)
    except NotFoundError as exc:
        names = ", ".join(sorted(t.name for t in _templates.list()))
        raise click.ClickException(
            f"unknown template {template!r}. Available templates: {names}"
        ) from exc


def _scaffold_component(name: str, ctype: str, directory: str):
    """Create a component .py and .yaml file from templates."""
    # Imported locally so these internals do not become public attributes of
    # this module.
    from ..component._manager._templates import (
        _TYPE_SKELETONS,
        render_manifest,
        render_script,
    )

    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)

    py_file = path / f"{name}.py"
    yaml_file = path / f"{name}.yaml"

    template = _TYPE_SKELETONS.get(ctype)
    if not template:
        click.echo(f"Unknown component type: {ctype}")
        return

    py_file.write_text(render_script(template["script"], name), encoding="utf-8")
    yaml_file.write_text(render_manifest(template["manifest"], name), encoding="utf-8")

    click.echo(f"  Created {py_file.name} + {yaml_file.name}")
