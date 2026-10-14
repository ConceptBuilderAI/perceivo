"""Mock call trace: records how each platform call was served.

After a run, the CLI reports, per platform call, the service invoked, what served
it, the resolved mode, and a short summary, in a form a program can parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The calls of the last local run to finish in this process, so
#: ``perceivo.local.trace()`` still answers after the run.
_LAST_RUN_CALLS: list[MockCall] = []


def last_run_calls() -> list[MockCall]:
    """The last finished local run's calls; empty before the first one."""
    return list(_LAST_RUN_CALLS)


@dataclass
class MockCall:
    """A single platform call served by a mock (or a real/local backend)."""

    service: str
    operation: str
    mode: str
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "operation": self.operation,
            "mode": self.mode,
            "summary": self.summary,
            "detail": self.detail,
        }


class MockTrace:
    """Ordered log of the platform calls a run made and how each was served."""

    def __init__(self) -> None:
        self._calls: list[MockCall] = []

    def record(
        self,
        service: str,
        operation: str,
        mode: str,
        summary: str = "",
        **detail: Any,
    ) -> None:
        self._calls.append(
            MockCall(
                service=service,
                operation=operation,
                mode=mode,
                summary=summary,
                detail=detail,
            )
        )

    @property
    def calls(self) -> list[MockCall]:
        return list(self._calls)

    def to_list(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._calls]

    def publish(self) -> None:
        """Publish this run's calls as the answer :func:`last_run_calls` gives after the run."""
        global _LAST_RUN_CALLS
        _LAST_RUN_CALLS = list(self._calls)

    def summary_by_service(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self._calls:
            counts[call.service] = counts.get(call.service, 0) + 1
        return counts
