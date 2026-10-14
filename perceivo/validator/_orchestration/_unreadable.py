"""Refuse an ``orchestration.yaml`` export cannot read, and say which refusals stand.

Export reads ``orchestration.yaml`` as UTF-8 and refuses the whole bundle when
it cannot, so validation names the file and why.
"""

from __future__ import annotations

from .._report import ValidationCheck
from ._loader import orchestration_unreadable

_MISSING = "Ensure orchestration.yaml exists."


def load_failure_check(project_dir: str) -> ValidationCheck:
    """The error for a project whose ``orchestration.yaml`` did not load."""
    unreadable = orchestration_unreadable(project_dir)
    return ValidationCheck(
        id="ORCH-LOAD-001",
        severity="error",
        category="graph",
        title="Failed to load orchestration",
        message=f"Could not load orchestration from {project_dir}: {unreadable}."
        if unreadable
        else f"Could not load orchestration from {project_dir}. {_MISSING}",
    )


def refuses_bundle(check: ValidationCheck) -> bool:
    """*check* reports a project file export cannot read.

    That error replaces the warning that the bundled ports went unchecked. A
    nested ``orchestration.yaml`` that is missing is not one: there is nothing
    to read, so the warning stands.
    """
    if check.id.endswith(("/VAL-RECORD-001", "/VAL-SCRIPT-001")):
        return True
    return check.id.endswith("/ORCH-LOAD-001") and not check.message.endswith(_MISSING)
