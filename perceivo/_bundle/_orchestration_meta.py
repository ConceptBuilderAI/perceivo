"""Orchestration-level bundle metadata carried through a local project.

``OrchestrationExportMetadata`` has seven fields. The project file keeps four of
them at its root (``name``, ``description``, ``tags``, ``runtime_config``). The
importer records the other three (``additional_packages``, ``requires_gpu``
and ``disable_intermediate_snapshots``) under ``orchestration_metadata`` when
the bundle carries them, and the exporter reads them back, so an import ->
export round trip preserves them.

The root ``additional_packages`` list is a different thing: the *union* of the
orchestration's and every component's declarations, recorded for the runner's
missing-package check. It is not the orchestration's own declaration and is
never exported as one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from perceivo._bundle.component import BundleConfigError
from perceivo._bundle.schema import OrchestrationExportMetadata

METADATA_KEY = "orchestration_metadata"
_ROOT_FIELDS = ("name", "description", "tags", "runtime_config")
_CARRIED = tuple(f for f in OrchestrationExportMetadata.model_fields if f not in _ROOT_FIELDS)


def carried_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """The metadata fields the project root does not hold, as the bundle had them."""
    return {field: meta[field] for field in _CARRIED if field in meta}


def export_metadata(data: dict[str, Any], project: Path) -> OrchestrationExportMetadata:
    """Rebuild the bundle's orchestration metadata from a project file."""
    carried = data.get(METADATA_KEY) or {}
    if not isinstance(carried, dict):
        raise BundleConfigError(f"'{METADATA_KEY}' must be a mapping")
    unknown = sorted(set(carried) - set(_CARRIED))
    if unknown:
        raise BundleConfigError(f"'{METADATA_KEY}' has undeclared fields: {unknown}")
    return OrchestrationExportMetadata(
        name=str(data.get("name") or project.name),
        description=data.get("description"),
        tags=data.get("tags"),
        runtime_config=data.get("runtime_config"),
        **carried,
    )
