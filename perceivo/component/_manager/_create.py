"""Component scaffolding: create() and _write_template()."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ._info import _VALID_TYPES
from ._templates import _TYPE_SKELETONS, render_manifest, render_script

logger = logging.getLogger("perceivo.component")


def create(
    name: str,
    component_type: str = "AGENT",
    directory: str = ".",
    *,
    description: str = "",
    tags: Optional[list[str]] = None,
) -> Path:
    """Create a new component by scaffolding script and manifest files.

    Args:
        name: Component display name. Used verbatim for the filenames and for
            the manifest's ``name``. It does not have to be a Python
            identifier: the scaffolded function name is derived from it by
            :func:`~perceivo.component._manager._templates.symbol_for`, so
            ``create("my-agent")`` writes ``my-agent.py`` containing
            ``async def my_agent(...)``.
        component_type: One of AGENT, TOOL, START, END, FSM, POLICY, ADAPTER.
        directory: Output directory (created if needed).
        description: Optional component description.
        tags: Optional list of tags.

    Returns:
        Path to the created directory.

    Raises:
        ValueError: If component_type is invalid.
    """
    ctype = component_type.upper().strip()
    if ctype not in _VALID_TYPES:
        raise ValueError(
            f"Invalid component type '{ctype}'. Must be one of: {sorted(_VALID_TYPES)}"
        )

    template_key = ctype.lower()
    if template_key not in _TYPE_SKELETONS:
        raise ValueError(f"No template for component type '{ctype}'.")

    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)

    _write_template(name, template_key, path)

    yaml_file = path / f"{name}.yaml"
    if yaml_file.exists() and (description or tags):
        import yaml

        manifest = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        if description:
            manifest["description"] = description
        if tags:
            manifest["tags"] = tags
        yaml_file.write_text(
            yaml.dump(manifest, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    logger.info("Created %s component '%s' at %s", ctype, name, path)
    return path


def _write_template(name: str, template_key: str, directory: Path) -> None:
    """Write component script + manifest from built-in templates."""
    template = _TYPE_SKELETONS.get(template_key)
    if not template:
        return

    py_file = directory / f"{name}.py"
    yaml_file = directory / f"{name}.yaml"

    py_file.write_text(render_script(template["script"], name), encoding="utf-8")
    yaml_file.write_text(render_manifest(template["manifest"], name), encoding="utf-8")
