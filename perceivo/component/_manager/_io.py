"""Save and load components in the platform ``ComponentExport`` bundle format.

``save_bundle`` takes no format argument. ``load_bundle`` dispatches on the
type of its first argument: a path reads a bundle from disk, a dict takes one
already in hand.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from ..._bundle.component import bundle_to_component, component_to_bundle

logger = logging.getLogger("perceivo.component")


def save_bundle(
    script_path: str,
    manifest_path: Optional[str] = None,
    *,
    output: Optional[str] = None,
) -> dict[str, Any]:
    """Export a component as a platform bundle.

    Args:
        script_path: Path to the component .py file.
        manifest_path: Path to the manifest .yaml file (auto-detected if omitted).
        output: If provided, write the bundle JSON to this file path.

    Returns:
        The bundle as a JSON-ready dict (``export_type: "component"``).
    """
    bundle = component_to_bundle(script_path, manifest_path)

    if output:
        # Reject traversal in the caller-supplied destination before writing.
        if ".." in Path(output).parts:
            raise ValueError(f"Output path must not contain '..': {output!r}")
        out_path = Path(output).resolve()
        out_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Exported component to %s", out_path)

    return bundle


def load_bundle(
    source: str | Path | dict[str, Any],
    directory: str = ".",
    *,
    name_override: Optional[str] = None,
) -> Path:
    """Import a component from a platform bundle.

    Args:
        source: A path to a bundle JSON file (``str`` / ``Path``), or a bundle
            already parsed into a ``dict``.
        directory: Output directory for the imported files.
        name_override: The file name stem, used exactly; one holding a path
            separator or ``..`` is refused. By default the bundle's display name,
            lowercased, with each run of other characters turned into ``_``.

    Returns:
        Path to the output directory.

    Raises:
        FileNotFoundError: If a source path does not exist.
        TypeError: If ``source`` is neither a path nor a dict.
        ValueError: If the payload is not a component bundle, the name is unsafe
            or too long, or files of that name already belong to a different
            component. Nothing is written when it is raised.
    """
    if isinstance(source, dict):
        bundle = source
    elif isinstance(source, (str, Path)):
        source_path = Path(source).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Bundle file not found: {source_path}")
        bundle = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        raise TypeError(
            "source must be a path (str | Path) or a parsed bundle (dict), "
            f"not {type(source).__name__}"
        )

    return bundle_to_component(bundle, directory, name_override=name_override)
