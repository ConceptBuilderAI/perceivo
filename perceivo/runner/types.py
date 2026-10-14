"""Data structures for the local orchestration runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PortConnection:
    """A connection from a source port to a target node."""

    to_node_id: str
    to_port_id: str
    to_component_type: str


@dataclass
class NodeShell:
    """Compiled representation of an orchestration node."""

    node_id: str
    name: str
    component_type: str
    script_path: Optional[str] = None
    manifest_path: Optional[str] = None
    project_path: Optional[str] = None
    config: dict[str, Any] = field(default_factory=dict)

    in_ports: list[dict[str, Any]] = field(default_factory=list)
    out_ports: list[dict[str, Any]] = field(default_factory=list)
    state_ports: list[dict[str, Any]] = field(default_factory=list)
    policy_ports: list[dict[str, Any]] = field(default_factory=list)

    invoke_connections: dict[str, list[PortConnection]] = field(default_factory=dict)
    mainflow_successors: list[str] = field(default_factory=list)

    input_parameters: dict[str, Any] = field(default_factory=dict)
    secret_bindings: dict[str, str] = field(default_factory=dict)
    fsm_states: list[str] = field(default_factory=list)


@dataclass
class CompileResult:
    """Result of compiling an orchestration graph."""

    root_id: str  # First node to execute (after START)
    start_node_id: str
    end_node_id: str
    shells_by_id: dict[str, NodeShell] = field(default_factory=dict)
    # Third-party distributions the orchestration declares; the runner reports the
    # ones this interpreter is missing.
    additional_packages: list[str] = field(default_factory=list)


@dataclass
class NodeTrace:
    """Per-node execution record for run feedback."""

    node: str
    component_type: str
    status: str = "ok"  # ok | failed
    input_keys: list[str] = field(default_factory=list)
    output_keys: list[str] = field(default_factory=list)
    mocks: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "component_type": self.component_type,
            "status": self.status,
            "input_keys": self.input_keys,
            "output_keys": self.output_keys,
            "mocks": self.mocks,
            "error": self.error,
        }


@dataclass
class ExecutionResult:
    """Result of running an orchestration or component."""

    success: bool
    artifact: Optional[Any] = None
    response_data: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    conversation_log: Optional[Any] = None
    # Run feedback: an ordered per-node execution trace and the full
    # list of platform calls served (each recording which mock served it).
    trace: list[NodeTrace] = field(default_factory=list)
    mock_calls: list[dict[str, Any]] = field(default_factory=list)
    # An END's terminal outcome, separate from ``success``: a raise loses the run's
    # output, while ``EndOutput(failed=True)`` completes the run and keeps it.
    terminal_failed: bool = False
    terminal_failure_reason: Optional[str] = None
    # Declared ``additional_packages`` not installed in this interpreter. Reported,
    # not fatal.
    missing_packages: list[str] = field(default_factory=list)
