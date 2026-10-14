"""Validation constants for orchestration graphs."""

from __future__ import annotations

MAX_FSM_DEPTH = 10

# Connection matrix for INVOKE edges only. MAINFLOW edges are not checked against
# it: on the platform the mainflow lives in node config, not the edge list.
ALLOWED_CONNECTIONS: dict[str, set[str]] = {
    "START": {"AGENT", "FSM", "ADAPTER", "ORCHESTRATION", "TOOL"},
    # END is terminal: the key is the source, so nothing may leave it.
    "END": set(),
    "AGENT": {"AGENT", "FSM", "TOOL", "REVIEW", "END", "ORCHESTRATION"},
    "FSM": {"AGENT", "FSM", "POLICY", "REVIEW", "END", "TOOL", "ADAPTER", "ORCHESTRATION"},
    "ADAPTER": {"AGENT", "FSM", "ADAPTER", "ORCHESTRATION", "END"},
    "TOOL": {"TOOL", "REVIEW", "AGENT", "FSM", "END", "ADAPTER", "ORCHESTRATION"},
    "POLICY": {"TOOL", "REVIEW"},
    "ORCHESTRATION": {"AGENT", "FSM", "ADAPTER", "ORCHESTRATION", "TOOL", "END"},
    "REVIEW": set(),
}

# Edge kinds the SDK project format materializes for the mainflow chain. The platform
# keeps the mainflow in node config, so per-edge structural checks skip them.
MAINFLOW_EDGE_KINDS = {"MAINFLOW"}

VALID_PORT_TYPES_BY_CATEGORY: dict[str, set[str]] = {
    "START": {"out", "external_in"},
    "END": {"in", "external_out"},
    "AGENT": {"in", "out", "tool", "review"},
    "FSM": {"in", "out", "state", "policy"},
    "TOOL": {"in", "out", "tool"},
    "POLICY": {"in", "out", "tool"},
    "ADAPTER": {"in", "out", "state"},
    "ORCHESTRATION": {"in", "out"},
    "REVIEW": {"in", "out"},
}

# Component types that may sit on the mainflow. TOOL is excluded: it cannot convert
# its in-port into a phantom out-port to carry the continuation.
MAINFLOW_COMPONENT_TYPES = {"AGENT", "FSM", "ADAPTER", "ORCHESTRATION"}
