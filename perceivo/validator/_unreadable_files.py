"""The refusals of a component file export cannot read."""

from __future__ import annotations

from ._report import ValidationCheck


def unreadable_script_check(reason: str) -> ValidationCheck:
    """The refusal of a script file that cannot be read; export refuses it too."""
    return ValidationCheck(
        id="VAL-SCRIPT-001",
        severity="error",
        category="interface",
        title="Script cannot be read",
        message=f"The script cannot be read: {reason}",
        suggestion="Save the script as UTF-8; the message names what is there now.",
    )


def unreadable_manifest_check(reason: str) -> ValidationCheck:
    """The refusal of a manifest file that cannot be read; export refuses it too."""
    return ValidationCheck(
        id="MAN-FORMAT-001",
        severity="error",
        category="manifest",
        title="Manifest cannot be read",
        message=f"The manifest cannot be read: {reason}",
        suggestion="Replace it with a UTF-8 YAML file; the message names what is there now.",
    )
