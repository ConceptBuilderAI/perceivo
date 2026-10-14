"""Execution events for streaming orchestration runs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """Types of execution events emitted during orchestration runs."""

    NODE_ENTER = "node_enter"
    NODE_EXIT = "node_exit"
    LOG = "log"
    TOKEN = "token"
    COST = "cost"
    ERROR = "error"
    COMPLETE = "complete"


@dataclass
class ExecutionEvent:
    """A single event emitted during orchestration execution."""

    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    node_name: Optional[str] = None
    node_type: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def node_enter(cls, name: str, node_type: str) -> ExecutionEvent:
        return cls(
            event_type=EventType.NODE_ENTER,
            node_name=name,
            node_type=node_type,
            message=f"Entering {node_type} node: {name}",
        )

    @classmethod
    def node_exit(cls, name: str, node_type: str, duration_ms: float = 0) -> ExecutionEvent:
        return cls(
            event_type=EventType.NODE_EXIT,
            node_name=name,
            node_type=node_type,
            data={"duration_ms": duration_ms},
            message=f"Exiting {node_type} node: {name} ({duration_ms:.0f}ms)",
        )

    @classmethod
    def log(
        cls, message: str, level: str = "INFO", node_name: Optional[str] = None
    ) -> ExecutionEvent:
        return cls(
            event_type=EventType.LOG,
            node_name=node_name,
            data={"level": level},
            message=message,
        )

    @classmethod
    def token(cls, text: str, node_name: Optional[str] = None) -> ExecutionEvent:
        return cls(
            event_type=EventType.TOKEN,
            node_name=node_name,
            data={"text": text},
            message=text,
        )

    @classmethod
    def cost(cls, amount: float, label: str, node_name: Optional[str] = None) -> ExecutionEvent:
        return cls(
            event_type=EventType.COST,
            node_name=node_name,
            data={"amount": amount, "label": label},
            message=f"Cost: ${amount:.6f} ({label})",
        )

    @classmethod
    def error(cls, error: str, node_name: Optional[str] = None) -> ExecutionEvent:
        return cls(
            event_type=EventType.ERROR,
            node_name=node_name,
            data={"error": error},
            message=f"Error: {error}",
        )

    @classmethod
    def complete(
        cls, success: bool = True, data: Optional[dict[str, Any]] = None
    ) -> ExecutionEvent:
        return cls(
            event_type=EventType.COMPLETE,
            data={"success": success, **(data or {})},
            message="Orchestration complete" if success else "Orchestration failed",
        )
