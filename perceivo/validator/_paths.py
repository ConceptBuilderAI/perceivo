"""Path-based component validation: what ``validate_component(path)`` reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from .._bundle._component_sidecar import read_manifest, recorded_fields
from .._bundle.schema import ComponentExportData
from ..types.errors import ConfigurationError
from ._component import RecordRefusal, validate_source
from ._component_type import ComponentTypeError, resolve_component_type
from ._report import ValidationReport

__all__ = ["validate_component", "validate_component_files"]

# A record's ``ports`` may itself be null, so None cannot mean "no record applies".
NOT_RECORDED: Any = object()


def recorded_ports(script: Path, manifest_yaml: Optional[str]) -> Any:
    """The port rows an imported component's record holds, while its manifest is unedited.

    The platform judges a component's port rules on those rows, not on the
    manifest, so an imported component is judged on what it will be created
    with. Once the manifest is edited it owns the ports, as it does on export.
    *manifest_yaml* must come from :func:`read_manifest`, the read the exporter
    hashes, and is None when no manifest is read; the record is then read
    against an empty manifest, as export reads it with none.

    The record is read by the exporter's own reader, in its order, so a record
    export refuses is refused here too, with or without a manifest: the result
    is then a :class:`RecordRefusal`. :data:`NOT_RECORDED` when there is no
    record, the manifest does not parse to a mapping (its own checks report
    that), or the record supplies no ports, so the manifest's ports stand as
    they do on export. Otherwise the recorded rows.
    """
    manifest_raw = manifest_yaml or ""
    try:
        manifest = yaml.safe_load(manifest_raw) or {}
    except yaml.YAMLError:
        return NOT_RECORDED
    if not isinstance(manifest, dict):
        return NOT_RECORDED
    from .._bundle.component import BundleConfigError

    try:
        fields = recorded_fields(script, manifest_raw, manifest)
    except BundleConfigError as exc:
        return RecordRefusal(str(exc))
    if fields is None:
        return NOT_RECORDED
    # Placeholders for the two fields export always takes from the manifest
    # when the record omits them; the record's own values replace them.
    try:
        ComponentExportData.model_validate({"name": "", "category": "", **fields})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return RecordRefusal(f"component field {where}: {first['msg']}")
    return fields.get("ports", NOT_RECORDED)


def _script_in(directory: Path) -> Path:
    """The one component script in *directory*."""
    # Export takes a directory only when it holds one script, so validation does too.
    scripts = sorted(p for p in directory.glob("*.py") if p.is_file())
    if len(scripts) == 1:
        return scripts[0]
    found = ", ".join(p.name for p in scripts) or "none"
    raise ConfigurationError(
        f"{directory} is not a component directory: it must hold exactly one "
        f"component script (.py); found {found}. Pass the script path instead."
    )


def validate_component(path: str | Path) -> ValidationReport:
    """Validate a component on disk.

    Args:
        path: The component's script file, or a directory holding exactly one
            component script. The manifest is the ``.yaml`` beside the script
            with the same name, as export reads it, and the component type
            is read from its ``extension_type``, else its ``category``, else
            the script's own decorator or base class.

    Returns:
        A :class:`ValidationReport` with all findings.

    Raises:
        FileNotFoundError: *path* does not exist.
        ConfigurationError: *path* is a directory with no single component script.

    Example:
        ```python
        report = perceivo.validator.validate_component("./my_project/agent.py")
        print(report.valid, [c.message for c in report.errors])
        ```
    """
    target = Path(path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Component not found: {target}")
    script = _script_in(target) if target.is_dir() else target
    return validate_component_files(script)


def validate_component_files(
    script_path: str | Path,
    manifest_path: Optional[str | Path] = None,
    component_type: Optional[str] = None,
) -> ValidationReport:
    """Validate a component script and its manifest, both by path."""
    script = Path(script_path).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")
    code, script_unreadable = read_text_or_reason(script)

    # With none named, the one manifest export reads.
    candidate = Path(manifest_path).resolve() if manifest_path else script.with_suffix(".yaml")
    manifest_yaml, unreadable = (
        read_text_or_reason(candidate) if candidate.exists() else (None, None)
    )

    if not component_type and not script_unreadable:
        try:
            component_type = resolve_component_type(str(script), None, manifest_yaml)
        except ComponentTypeError:
            # Left unset, so the report carries VAL-TYPE rather than raising.
            component_type = None

    rows = recorded_ports(script, manifest_yaml)
    recorded = rows is not NOT_RECORDED
    return validate_source(
        script=code or "",
        manifest_yaml=manifest_yaml,
        component_type=component_type,
        ports=rows if recorded else None,
        ports_recorded=recorded,
        script_unreadable=script_unreadable,
        manifest_unreadable=unreadable,
    )


def read_text_or_reason(
    path: Path, subject: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """The file at *path* as export reads it, or None and why it cannot be read.

    The reason starts with *subject*, the file's name unless given.
    """
    subject = subject or path.name
    if path.is_dir():
        return None, f"{subject} is a directory, not a file"
    try:
        return read_manifest(path), None
    except UnicodeDecodeError as exc:
        return None, f"{subject} is not UTF-8 text (byte {exc.start})"
    except OSError as exc:
        return None, f"{subject}: {exc.strerror or exc}"
