"""
Artifact types for perceivo SDK.

Artifacts are the primary data containers passed between components in an orchestration.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ArtifactType(str, Enum):
    """
    Enumeration of artifact types.

    Artifacts carry data between components. The type indicates how the data
    should be interpreted and displayed.
    """

    NONE = "none"
    TEXT = "text"
    JSON = "json"
    FILE = "file"


@dataclass
class Artifact:
    """
    Data container passed between components.

    Artifacts carry the primary data flowing through an orchestration.
    Each component receives an artifact from its predecessor and produces
    an artifact for its successor.

    Attributes:
        type: The type of artifact (text, json, file, or none).
        data: The artifact's data payload. Type depends on artifact type:
            - TEXT: str
            - JSON: dict or list
            - FILE: bytes or str (URL/path)
            - NONE: None
        mime_type: MIME type for the data (default: "text/plain").
        file_name: Optional filename when type is FILE.

    Example:
        ```python
        # Text artifact
        text_artifact = perceivo.primitives.Artifact(type="text", data="Hello, world!")

        # JSON artifact
        json_artifact = perceivo.primitives.Artifact(
            type="json",
            data={"key": "value"},
            mime_type="application/json"
        )

        # File artifact
        file_artifact = perceivo.primitives.Artifact(
            type="file",
            data=file_bytes,
            mime_type="application/pdf",
            file_name="report.pdf"
        )
        ```
    """

    type: ArtifactType
    data: Any
    mime_type: str = "text/plain"
    file_name: Optional[str] = None
    name: Optional[str] = None  # Name when part of a multi-artifact set

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = ArtifactType(self.type)

    def __getitem__(self, key: str) -> Any:
        """Return an artifact field by key, as for a mapping."""
        if key not in {"type", "data", "mime_type", "file_name", "name"}:
            raise KeyError(key)
        value = getattr(self, key)
        return value.value if isinstance(value, ArtifactType) else value

    def get(self, key: str, default: Any = None) -> Any:
        """Return an artifact field by key, or *default* if it is absent."""
        try:
            return self[key]
        except KeyError:
            return default
