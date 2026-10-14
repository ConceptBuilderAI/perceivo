"""Orchestration graph validation.

Validates the structure, connections, and contracts of a complete
orchestration defined by nodes, edges, and ports, either loaded
from a project directory or passed as data structures.
"""

from ._constants import (
    ALLOWED_CONNECTIONS,
    MAINFLOW_COMPONENT_TYPES,
    MAX_FSM_DEPTH,
    VALID_PORT_TYPES_BY_CATEGORY,
)
from ._main import validate_orchestration

__all__ = [
    "ALLOWED_CONNECTIONS",
    "MAINFLOW_COMPONENT_TYPES",
    "MAX_FSM_DEPTH",
    "VALID_PORT_TYPES_BY_CATEGORY",
    "validate_orchestration",
]
