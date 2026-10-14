"""Validate components and orchestrations locally.

Runs the same checks the Perceivo platform applies on import.

    from perceivo.validator import validate_component, validate_orchestration

    # Validate a component: its script file, or a directory holding one
    report = validate_component("./my-orchestration/agent.py")

    # Validate an orchestration project directory
    report = validate_orchestration("./my-orchestration/")

Both take a path and nothing else about the component; the orchestration
validator also takes ``validate_scripts=``. The manifest is read from beside the
script and the component type from its ``extension_type``.
"""

from ._orchestration import validate_orchestration
from ._paths import validate_component
from ._report import ValidationCheck, ValidationReport

__all__ = [
    "ValidationCheck",
    "ValidationReport",
    "validate_component",
    "validate_orchestration",
]
