"""Local orchestration executor: runs orchestrations in a single process.

Walks the mainflow chain, executes components via ENTRY_CLASS, and
resolves invoke_port calls as in-process async function calls.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from contextvars import ContextVar
from pathlib import Path as _Path
from time import perf_counter as _perf_counter
from typing import Any, Optional

# Aliased private so they do not become public members of this module.
from .._internal.packages import missing_packages as _missing_packages
from .._internal.packages import missing_packages_warning as _missing_packages_warning
from ..config import load
from ..local.context import LocalExtensionManager
from ..log._logger import flush_pending_writes as _flush_log_writes
from ..types.artifact import Artifact
from ._component_execution import apply_result_artifacts
from ._component_execution import execute_component as _execute_component
from ._component_run import run_component_body as _run_component
from ._configuration import (
    configure_ext_from_shell as _configure_ext_from_shell,
)
from ._configuration import reset_ext_node_context as _reset_ext_node_context
from ._emit import emit as _emit
from ._nested import UnreadableChildError as _UnreadableChildError
from ._nested import execute_nested as _execute_nested
from ._ports import invoke_port_parallel as _invoke_port_parallel
from ._ports import invoke_single_port as _invoke_single_port
from ._recording import orchestration_name as _orchestration_name
from ._recording import recorded_run as _recorded_run
from ._recording import run_config_path as _run_config_path
from ._start_input import start_input_error as _start_input_error
from .compiler import compile_project
from .events import ExecutionEvent as _ExecutionEvent
from .loader import load_component_class
from .types import CompileResult, ExecutionResult, NodeTrace

logger = logging.getLogger("perceivo.runner")


async def _run_setup_hook(instance: Any, name: str) -> None:
    """Call the instance's async ``setup`` if it has one; a failure only warns.

    A child it reaches that cannot be read is refused as anywhere else in the run.
    """
    setup_hook = getattr(instance, "setup", None)
    if asyncio.iscoroutinefunction(setup_hook):
        try:
            await setup_hook()
        except _UnreadableChildError:
            raise
        except Exception as exc:
            logger.warning("Setup failed for %s: %s", name, exc)


async def run_orchestration(
    project_dir: str,
    *,
    input_data: Optional[dict[str, Any]] = None,
    input_artifact: Optional[Artifact] = None,
    config_path: Optional[str] = None,
) -> ExecutionResult:
    """Run a complete orchestration from a project directory.

    Compiles the orchestration graph, loads all components, and executes
    them in mainflow order. Port invocations are resolved in-process.

    Args:
        project_dir: Path to the orchestration project directory.
        input_data: Optional input data dict (passed to START).
        input_artifact: Optional input artifact (passed to START).
        config_path: Path to perceivo.yaml (defaults to auto-detect).

    Returns:
        An :class:`ExecutionResult` with the final output.

    Every call writes exactly one run record under ``<home>/runs/`` and emits
    the execution events ``run_stream`` yields. With no ``config_path``, the
    ``perceivo.yaml`` used is ``PERCEIVO_CONFIG``, else the nearest one at or
    above ``project_dir``. When the START declares request data, ``input_data``
    must match it, with no undeclared key and every required field present, or the
    run fails before START runs. The declaration is the ``request_data`` of the
    first mapping port in the port value, which is ``external_in_port``, or
    ``external_in_ports`` when ``external_in_port`` is missing or falsy (null, an
    empty mapping, list or string, 0 or false); when there is no such port or it
    declares no fields, it is the manifest's ``request_data_schema``. A port value
    that is neither falsy nor iterable, such as a non-zero number, true or a date,
    declares nothing, and the input is not checked.
    """
    config_file = _run_config_path(project_dir, config_path)
    with _recorded_run(
        project_dir,
        name=_orchestration_name(project_dir),
        kind="orchestration",
        input_data=input_data,
        config_path=config_file,
    ) as run:
        result = await _run_orchestration(project_dir, input_data, input_artifact, config_file)
        run.finish(result)
    return result


async def _run_orchestration(
    project_dir: str,
    input_data: Optional[dict[str, Any]],
    input_artifact: Optional[Artifact],
    config_path: Optional[str],
) -> ExecutionResult:
    config = load(config_path)
    ext = LocalExtensionManager(config)

    try:
        plan = compile_project(project_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        return ExecutionResult(success=False, error=str(exc))

    input_error = _start_input_error(plan.shells_by_id[plan.start_node_id], input_data or {})
    if input_error is not None:
        return ExecutionResult(success=False, error=input_error)

    # Report declared packages missing from this interpreter before the run.
    missing = _missing_packages(plan.additional_packages)
    if missing:
        logger.warning("%s", _missing_packages_warning(missing, where="This orchestration"))

    executor = _OrchestrationExecutor(plan, ext)

    try:
        result = await executor.execute(
            input_data=input_data or {},
            input_artifact=input_artifact,
        )
    except _UnreadableChildError:
        # Refused in validation's words, not a crash to log.
        raise
    except Exception as exc:
        logger.exception("Orchestration execution failed")
        result = ExecutionResult(success=False, error=str(exc))

    await _flush_log_writes()
    # Attach the run feedback: the per-node trace and every mock call served.
    result.trace = list(executor._trace)
    result.mock_calls = ext._mocks.trace.to_list()
    result.missing_packages = missing
    return result


async def run_component(
    script_path: str,
    *,
    component_type: str = "AGENT",
    input_data: Optional[dict[str, Any]] = None,
    input_artifact: Optional[Artifact] = None,
    manifest_path: Optional[str] = None,
    config_path: Optional[str] = None,
) -> ExecutionResult:
    """Run a single component script.

    Loads the component, creates a local execution context, and calls
    the component's entry method (run/start/end).

    Args:
        script_path: Path to the component .py file.
        component_type: Component type (default: AGENT).
        input_data: Optional input data dict.
        input_artifact: Optional input artifact.
        manifest_path: Optional path to the component's YAML manifest.
        config_path: Path to perceivo.yaml (defaults to auto-detect).

    Returns:
        An :class:`ExecutionResult` with the component's output.

    Every call writes exactly one run record (``kind="component"``) under the
    ``<home>/runs/`` of the script's project.
    """
    script_dir = str(_Path(script_path).resolve().parent)
    config_file = _run_config_path(script_dir, config_path)
    with _recorded_run(
        script_dir,
        name=_Path(script_path).stem,
        kind="component",
        input_data=input_data,
        config_path=config_file,
    ) as run:
        result = await _run_component(
            script_path, component_type, input_data, input_artifact, manifest_path, config_file
        )
        run.finish(result)
    return result


class _OrchestrationExecutor:
    """Single-process orchestration executor."""

    def __init__(self, plan: CompileResult, ext: LocalExtensionManager):
        self._plan = plan
        self._ext = ext
        self._component_instances: dict[str, Any] = {}
        self._node_outputs: dict[str, dict[str, Any]] = {}
        self._trace: list[NodeTrace] = []
        # The END outcome is executor state, recorded wherever an END is dispatched.
        self._terminal_failed = False
        self._terminal_failure_reason: Optional[str] = None
        # Number of enclosing ``inline`` ORCHESTRATION levels; capped at MAX_INLINE_DEPTH.
        self._inline_depth = 0
        # Whether any END outcome has been recorded, so a subprocess child that
        # never reached an END hands nothing up to its parent.
        self._end_verdict_recorded = False
        # The artifact held once START has run. A subprocess child whose END returns
        # none hands this one up instead.
        self._start_artifact: Optional[Artifact] = None
        self._active_node_id: ContextVar[Optional[str]] = ContextVar(
            f"perceivo_active_node_{id(self)}", default=None
        )
        # An FSM node's input, by node id, while that node runs with
        # ``forward_input_to_states`` set; single port invokes merge it in.
        self._fsm_forward_data: dict[str, dict[str, Any]] = {}
        # The run's named artifacts, which every node's input carries a copy of.
        self._artifacts: dict[str, Any] = {}

    async def execute(
        self,
        input_data: dict[str, Any],
        input_artifact: Optional[Artifact] = None,
    ) -> ExecutionResult:
        """Execute the orchestration."""
        callback_tokens = self._activate_callbacks()
        self._ext.activate()
        try:
            return await self._execute_active(input_data, input_artifact)
        finally:
            self._ext.deactivate()
            self._ext.reset_invoke_port_parallel_callback(callback_tokens[1])
            self._ext.reset_invoke_port_callback(callback_tokens[0])

    async def _execute_active(
        self,
        input_data: dict[str, Any],
        input_artifact: Optional[Artifact],
        subprocess_child: bool = False,
    ) -> ExecutionResult:
        """Execute while this executor owns the active SDK context and callbacks.

        A ``subprocess_child`` run applies its START and END results as the
        engine applies a nested orchestration's, not a top-level run's.
        """
        load_error = self._load_all_instances()
        if load_error is not None:
            return load_error

        # The engine starts a run with no artifact when none is given.
        artifact = input_artifact
        request_data = dict(input_data)

        # The run's artifacts start as its input artifact, as "main", or empty.
        artifacts = self._artifacts = {"main": artifact} if artifact else {}
        start_result = await self._execute_node(self._plan.start_node_id, artifact, request_data)
        if subprocess_child:
            # A nested START adds to the artifacts and keeps the incoming artifact
            # unless it returns one.
            artifacts.update(start_result.get("artifacts") or {})
            if start_result.get("artifact"):
                artifact = start_result["artifact"]
            request_data = start_result.get("response_data", request_data)
        else:
            apply_result_artifacts(artifacts, start_result)
            # A node that returns no artifact clears the one carried on.
            if "artifact" in start_result:
                artifact = start_result["artifact"]
            request_data = start_result.get("response_data")

        self._start_artifact = artifact
        artifact, request_data = await self._walk_mainflow(artifact, request_data, artifacts)

        end_result = await self._execute_node(self._plan.end_node_id, artifact, request_data)
        apply_result_artifacts(artifacts, end_result)
        if not subprocess_child and end_result.get("artifact"):
            # A top-level run holds END's artifact as "main" beside its named ones.
            artifacts["main"] = end_result["artifact"]

        # A failed END still owns the run's output; only the outcome changes. The last
        # END to run, wherever it was dispatched, decides the outcome.
        return ExecutionResult(
            success=not self._terminal_failed,
            artifact=end_result.get("artifact", artifact),
            # The engine's run result starts empty and takes END's response data only
            # when it is non-empty.
            response_data=end_result.get("response_data") or {},
            artifacts=artifacts,
            conversation_log=end_result.get("conversation_log"),
            error=self._terminal_failure_reason,
            terminal_failed=self._terminal_failed,
            terminal_failure_reason=self._terminal_failure_reason,
        )

    def _activate_callbacks(self) -> tuple[Any, Any]:
        """Make this executor the active invoke target for shared local context."""
        # ``functools.partial`` through the module, so no class is bound into this
        # module's public namespace.
        invoke_token = self._ext.set_invoke_port_callback(
            functools.partial(_invoke_single_port, self)
        )
        parallel_token = self._ext.set_invoke_port_parallel_callback(
            functools.partial(_invoke_port_parallel, self)
        )
        return invoke_token, parallel_token

    def _load_all_instances(self) -> Optional[ExecutionResult]:
        """Load every component instance up front.

        Returns a failed :class:`ExecutionResult` if any component fails to
        load, otherwise ``None``.
        """
        for node_id, shell in self._plan.shells_by_id.items():
            if shell.script_path:
                try:
                    instance = load_component_class(shell.script_path)
                    self._component_instances[node_id] = instance
                except Exception as exc:
                    return ExecutionResult(
                        success=False,
                        error=f"Failed to load {shell.name}: {exc}",
                    )
        return None

    async def _walk_mainflow(
        self,
        artifact: Optional[Artifact],
        request_data: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> tuple[Optional[Artifact], dict[str, Any]]:
        """Walk the mainflow chain root → ... → END, executing each node.

        ``artifacts`` is replaced in place by each node's result; the evolving ``artifact`` and
        ``request_data`` are returned for the END node.
        """
        current_id = self._plan.root_id
        while current_id and current_id != self._plan.end_node_id:
            shell = self._plan.shells_by_id.get(current_id)
            if not shell:
                break

            result = await self._execute_node(current_id, artifact, request_data)
            self._node_outputs[current_id] = result

            if "artifact" in result:
                artifact = result["artifact"]
            apply_result_artifacts(artifacts, result)
            # The engine hands on the previous response data when a node returns none.
            if result.get("response_data"):
                request_data = result["response_data"]

            if shell.mainflow_successors:
                current_id = shell.mainflow_successors[0]
            else:
                break

        return artifact, request_data

    async def _execute_node(
        self,
        node_id: str,
        artifact: Optional[Artifact],
        request_data: dict[str, Any],
        artifacts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a single node, emitting its ``node_enter``/``node_exit`` as it runs.

        A node that raises emits ``error`` for itself before the exception
        propagates. Every dispatch path (mainflow, invoke ports and nested
        children) comes through here, so each node reports its own events in
        execution order.
        """
        shell = self._plan.shells_by_id[node_id]
        _emit(_ExecutionEvent.node_enter(shell.name, shell.component_type))
        started = _perf_counter()
        try:
            output = await self._run_node(node_id, artifact, request_data, artifacts)
        except Exception as exc:
            await _flush_log_writes()
            _emit(_ExecutionEvent.error(str(exc), node_name=shell.name))
            raise
        # The node's own log lines print before the runner moves to the next node.
        await _flush_log_writes()
        duration_ms = (_perf_counter() - started) * 1000
        _emit(_ExecutionEvent.node_exit(shell.name, shell.component_type, duration_ms=duration_ms))
        return output

    async def _run_node(
        self,
        node_id: str,
        artifact: Optional[Artifact],
        request_data: dict[str, Any],
        artifacts: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a single node and return its output.

        The node's input carries ``artifacts``, or the run's when none are given.
        """
        # The engine gives a node empty request data when none was handed on.
        request_data = request_data or {}
        shell = self._plan.shells_by_id[node_id]
        instance = self._component_instances.get(node_id)

        if shell.component_type == "ORCHESTRATION":
            return await _execute_nested(self, shell, artifact, request_data)

        if instance is None:
            logger.warning("No component loaded for node %s (%s)", shell.name, shell.component_type)
            self._trace.append(
                NodeTrace(
                    node=shell.name,
                    component_type=shell.component_type,
                    status="skipped",
                    input_keys=sorted(request_data.keys()),
                )
            )
            skipped = {"artifact": artifact, "response_data": request_data}
            if shell.component_type == "REVIEW":
                # A REVIEW node has no script on the platform and passes the run's
                # artifacts on.
                skipped["artifacts"] = dict(self._artifacts if artifacts is None else artifacts)
            return skipped

        logger.info("Executing: %s (%s)", shell.name, shell.component_type)

        env_token, secret_token = _configure_ext_from_shell(self._ext, shell)

        mock_before = len(self._ext._mocks.trace.calls)
        if shell.component_type == "FSM" and shell.config.get("forward_input_to_states"):
            if request_data:
                self._fsm_forward_data[node_id] = dict(request_data)
        token = self._active_node_id.set(node_id)
        try:
            await _run_setup_hook(instance, shell.name)

            result = await _execute_component(
                instance,
                shell.component_type,
                artifact,
                request_data,
                self._artifacts if artifacts is None else artifacts,
            )
        finally:
            self._active_node_id.reset(token)
            self._fsm_forward_data.pop(node_id, None)
            _reset_ext_node_context(self._ext, env_token, secret_token)

        node_mocks = [c.to_dict() for c in self._ext._mocks.trace.calls[mock_before:]]
        # A terminal-failed END did not raise, but the trace still reports it as failed.
        node_failed = (not result.success) or result.terminal_failed
        self._trace.append(
            NodeTrace(
                node=shell.name,
                component_type=shell.component_type,
                status="ok" if not node_failed else "failed",
                input_keys=sorted(request_data.keys()),
                output_keys=sorted((result.response_data or {}).keys()),
                mocks=node_mocks,
                error=(
                    None if not node_failed else (result.error or result.terminal_failure_reason)
                ),
            )
        )

        if shell.component_type == "END" or result.terminal_failed:
            self._record_terminal_failure(result)

        # A terminal END outcome is the run's answer, not a crash: return normally and
        # keep the output.
        if not result.success and not result.terminal_failed:
            raise RuntimeError(f"Component {shell.name} failed: {result.error}")

        return {
            "artifact": result.artifact,
            "response_data": result.response_data,
            "artifacts": result.artifacts,
            "conversation_log": result.conversation_log,
            "terminal_failed": result.terminal_failed,
            "terminal_failure_reason": result.terminal_failure_reason,
        }

    def _record_terminal_failure(self, result: ExecutionResult) -> None:
        """Record an END's outcome for the run result; the last END wins.

        A later clean END clears an earlier failed one. The reason is read from
        ``terminal_failure_reason`` first, falling back to ``error``.
        """
        self._end_verdict_recorded = True
        self._terminal_failed = bool(result.terminal_failed)
        self._terminal_failure_reason = (
            (result.terminal_failure_reason or result.error) if self._terminal_failed else None
        )
