"""Inspect components with validate(), details() and list_components()."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ...validator import ValidationReport

from ._info import ComponentInfo, _detect_entry_class


def validate(
    script_path: str,
    manifest_path: Optional[str] = None,
    *,
    component_type: Optional[str] = None,
) -> ValidationReport:
    """Validate a component's script and manifest.

    Args:
        script_path: Path to the component .py file.
        manifest_path: Path to the manifest .yaml file (auto-detected if omitted).
        component_type: Override component type (auto-detected from manifest if omitted).

    Returns:
        A :class:`~perceivo.validator.ValidationReport`.
    """
    from ...validator._paths import validate_component_files

    return validate_component_files(script_path, manifest_path, component_type)


def details(
    script_path: str,
    manifest_path: Optional[str] = None,
) -> ComponentInfo:
    """Load and inspect a component's metadata.

    Args:
        script_path: Path to the component .py file.
        manifest_path: Path to the manifest .yaml file (auto-detected if omitted).

    Returns:
        A :class:`ComponentInfo` with the component's metadata.
    """
    import yaml

    script = Path(script_path).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")

    manifest: dict[str, Any] = {}
    mp: Optional[Path] = None
    if manifest_path:
        mp = Path(manifest_path).resolve()
    else:
        candidate = script.with_suffix(".yaml")
        if candidate.exists():
            mp = candidate

    if mp and mp.exists():
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}

    code = script.read_text(encoding="utf-8")
    entry_class = _detect_entry_class(code)

    from ...validator._port_rules import _manifest_ports

    return ComponentInfo(
        name=manifest.get("name", script.stem),
        component_type=manifest.get("extension_type", "UNKNOWN"),
        description=manifest.get("description", ""),
        tags=manifest.get("tags", []),
        script_path=str(script),
        manifest_path=str(mp) if mp else "",
        ports=_manifest_ports(manifest),
        input_parameters=manifest.get("input_parameters", []),
        required_secrets=manifest.get("required_secrets", []),
        fsm_states=manifest.get("fsm_states", []),
        entry_class=entry_class,
    )


def list_components(
    directory: str = ".",
    *,
    recursive: bool = False,
) -> list[ComponentInfo]:
    """List all components in a directory.

    Finds all .py files that have a matching .yaml manifest and returns
    their metadata.

    Args:
        directory: Directory to search.
        recursive: If True, search subdirectories.

    Returns:
        List of :class:`ComponentInfo` objects.
    """
    path = Path(directory).resolve()
    if not path.is_dir():
        return []

    pattern = "**/*.yaml" if recursive else "*.yaml"
    results: list[ComponentInfo] = []

    for yaml_file in sorted(path.glob(pattern)):
        py_file = yaml_file.with_suffix(".py")
        if not py_file.exists():
            continue

        try:
            import yaml

            manifest = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            if "extension_type" not in manifest:
                continue

            info = details(str(py_file), str(yaml_file))
            results.append(info)
        except Exception:
            continue

    return results
