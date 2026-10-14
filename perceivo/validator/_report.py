"""Validation report data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ValidationCheck:
    """A single validation finding."""

    id: str
    severity: str  # "error" | "warning" | "info"
    category: str  # "syntax" | "security" | "interface" | "contract" | "graph" | "manifest"
    title: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class ValidationReport:
    """Complete validation report for a component or orchestration."""

    valid: bool
    checks: list[ValidationCheck] = field(default_factory=list)
    runtime_observations: dict[str, Any] = field(default_factory=lambda: {"calls": [], "hosts": []})

    @property
    def errors(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.severity == "error"]

    @property
    def warnings(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.severity == "warning"]

    @property
    def info(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.severity == "info"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "info": len(self.info),
            },
            "checks": [
                {
                    "id": c.id,
                    "severity": c.severity,
                    "category": c.category,
                    "title": c.title,
                    "message": c.message,
                    "location": (
                        {"line": c.line, "column": c.column or 1} if c.line is not None else None
                    ),
                    "suggestion": c.suggestion,
                }
                for c in self.checks
            ],
            "runtime_observations": self.runtime_observations,
        }

    def format(self) -> str:
        """Format the report for terminal display."""
        lines: list[str] = []
        status = "PASSED" if self.valid else "FAILED"
        lines.append(f"Validation {status}")
        lines.append(
            f"  {len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.info)} info"
        )
        lines.append("")

        for check in self.checks:
            icon = {"error": "E", "warning": "W", "info": "I"}.get(check.severity, "?")
            loc = f":{check.line}" if check.line else ""
            lines.append(f"  [{icon}] {check.id}{loc} — {check.message}")
            if check.suggestion:
                lines.append(f"      Suggestion: {check.suggestion}")

        return "\n".join(lines)
