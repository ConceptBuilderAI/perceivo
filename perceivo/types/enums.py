"""Define the shared vocabulary enums.

Every one is a :class:`~enum.StrEnum`, so a literal string is always
accepted::

    Severity.WARNING == "warning"     # True

An enum that belongs to one plane lives on that plane instead:
:class:`perceivo.log.LogType`, :class:`perceivo.files.StorageContainer` and
:class:`perceivo.reviews.ReviewSubject`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AIService",
    "Level",
    "Runner",
    "Severity",
]


class Runner(StrEnum):
    """The execution runtimes a run record can belong to."""

    ORCHESTRATION = "orchestration"
    CAPABILITY = "capability"
    FEATURE = "feature"
    TRAINING = "training"
    INFERENCE = "inference"
    EVALUATION = "evaluation"


class Severity(StrEnum):
    """Alert severity.

    Taken by both ``perceivo.alerts.send`` and ``perceivo.alerts.send_direct``.
    The members are the three values both delivery routes validate against.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Level(StrEnum):
    """Log levels.

    Not interchangeable with :class:`Severity`: ``Severity`` has no ``debug``
    and no ``error``, and these levels have no ``critical``.

    Nothing validates a level: an unrecognised value is recorded as INFO rather
    than rejected. A bare string still passes; the enum names the four working
    spellings so an editor can catch a typo.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AIService(StrEnum):
    """The managed AI capabilities reachable through one verb.

    One ``invoke(service, ...)`` serves them all, because the services share a
    dispatch path and differ only in payload.
    """

    CHAT = "chat"
    EMBEDDING = "embedding"
    CONTENT_SAFETY = "content_safety"
    GROUNDEDNESS = "groundedness"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    VISION = "vision"
    TRANSLATION = "translation"
    DOCUMENT_INTEL = "document_intelligence"
