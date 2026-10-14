"""Parse component source pairs once while an orchestration bundle is built."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from perceivo._bundle.component import component_to_bundle
from perceivo._bundle.schema import ComponentExportData
from perceivo._internal.paths import resolve_within_project


def component_base(
    project: Path,
    nid: str,
    script: Any,
    manifest: Any,
    files_key: tuple[str, str],
    parsed_by_files: dict[tuple[str, str], ComponentExportData],
) -> ComponentExportData:
    """Return the parsed component for a script/manifest pair, parsing it once."""
    base = parsed_by_files.get(files_key)
    if base is None:
        cbundle = component_to_bundle(
            resolve_within_project(project, script, field="script", node_id=nid),
            resolve_within_project(project, manifest, field="manifest", node_id=nid)
            if manifest
            else None,
        )
        base = ComponentExportData.model_validate(cbundle["component"])
        parsed_by_files[files_key] = base
    return base
