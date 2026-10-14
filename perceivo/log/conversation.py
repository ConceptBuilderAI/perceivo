"""Define the conversation model, ``perceivo.log.conversation``.

A conversation is a log: agents write it (``AgentOutput.conversation_log``),
policies read it, and the stream plane sends and receives it. Each message is
structured and typed, so downstream agents and the platform can inspect it
programmatically.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["ContentPart", "ConversationMessage"]


@dataclass
class ContentPart:
    """
    Individual content unit within a conversation message.

    Supports typed, named content sections so downstream agents can
    programmatically extract specific parts of a conversation entry.

    Attributes:
        content_type: MIME type of the content.
            Common values: "text/plain", "text/markdown", "application/json"
        content: The actual content. String for text types, dict/list for JSON.
        name: Optional label identifying this part's role within the message.
            Convention: "summary", "response", "reasoning", "analysis",
            "synthesis", "metrics", "feedback", "tool_result", "error"
    """

    content_type: str
    content: Any
    name: Optional[str] = None


@dataclass
class ConversationMessage:
    """Structured conversation log entry for multi-agent collaboration.

    Each message contains one or more typed :class:`ContentPart` objects, so
    downstream agents and the platform can inspect conversation history
    programmatically.

    Attributes:
        role: Message author role. Always "agent" for agent-produced messages.
        parts: Ordered list of content parts making up this message.
        metadata: Optional dict for token counts, model used, timing, etc.

    Example:
        ```python
        ConversationMessage(
            role="agent",
            parts=[
                ContentPart("text/plain", "Analyzed 3 anomalies.", name="summary"),
                ContentPart("application/json", {"count": 3}, name="metrics"),
            ],
            metadata={"model": "gpt-4o", "input_tokens": 1200}
        )
        ```
    """

    role: str
    parts: list[ContentPart] = field(default_factory=list)
    metadata: Optional[dict[str, Any]] = None

    @property
    def text(self) -> str:
        """Concatenate all text/* parts into a single string."""
        texts = []
        for p in self.parts:
            if isinstance(p.content, str) and p.content_type.startswith("text/"):
                texts.append(p.content)
        return "\n".join(texts)

    def get_part(self, name: str) -> Optional[ContentPart]:
        """Get the first content part with the given name."""
        return next((p for p in self.parts if p.name == name), None)

    def get_parts(self, content_type: str) -> list[ContentPart]:
        """Get all content parts matching the given MIME type."""
        return [p for p in self.parts if p.content_type == content_type]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Loki/Redis storage."""
        result: dict[str, Any] = {
            "role": self.role,
            "parts": [
                {
                    "content_type": p.content_type,
                    "content": p.content,
                    **({"name": p.name} if p.name else {}),
                }
                for p in self.parts
            ],
        }
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationMessage":
        """Deserialize from Loki/Redis storage."""
        return cls(
            role=data.get("role", "agent"),
            parts=[
                ContentPart(
                    content_type=p["content_type"],
                    content=p["content"],
                    name=p.get("name"),
                )
                for p in data.get("parts", [])
            ],
            metadata=data.get("metadata"),
        )
