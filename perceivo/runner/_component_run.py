"""The body of ``run_component``: one component, run as a whole local run.

``executor.run_component`` wraps it in the run's history record.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace as _replace
from typing import Any, Optional

from ..config import load
from ..local.context import LocalExtensionManager
from ..log._logger import flush_pending_writes as _flush_log_writes
from ..types.artifact import Artifact
from ..validator._component_tables import CLASS_ENTRY_TYPES
from ._component_execution import execute_component as _execute_component
from ._configuration import configure_ext_from_manifest as _configure_ext_from_manifest
from .loader import load_component_class, load_manifest
from .types import ExecutionResult

logger = logging.getLogger("perceivo.runner")


async def run_component_body(
    script_path: str,
    component_type: str,
    input_data: Optional[dict[str, Any]],
    input_artifact: Optional[Artifact],
    manifest_path: Optional[str],
    config_path: Optional[str],
) -> ExecutionResult:
    config = load(config_path)
    ext = LocalExtensionManager(config)
    result: Optional[ExecutionResult] = None

    if manifest_path:
        manifest = load_manifest(manifest_path)
        _configure_ext_from_manifest(ext, manifest)

    ext.activate()

    try:
        instance = load_component_class(
            script_path,
            accept_class=component_type.upper().strip() in CLASS_ENTRY_TYPES,
        )

        setup_hook = getattr(instance, "setup", None)
        if asyncio.iscoroutinefunction(setup_hook):
            await setup_hook()

        ctype = component_type.upper().strip()
        # A component given no artifact receives None, as a node does in a run.
        artifact = input_artifact
        request_data = input_data or {}

        result = await _execute_component(instance, ctype, artifact, request_data)
        # One component is the whole run here, so a terminal-failed END fails the run,
        # as it does in ``run_orchestration``.
        if result.terminal_failed:
            result = _replace(
                result,
                success=False,
                error=result.error or result.terminal_failure_reason,
            )
        return result
    except Exception as exc:
        logger.exception("Component execution failed: %s", exc)
        result = ExecutionResult(success=False, error=str(exc))
        return result
    finally:
        # Every log line the component wrote is out before the run returns.
        await _flush_log_writes()
        ext.deactivate()
        # Attached in ``finally`` so the failure paths carry the mock trace too.
        if result is not None:
            result.mock_calls = ext._mocks.trace.to_list()
