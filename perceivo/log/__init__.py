"""The log plane.

**Write.** Four convenience levels and one async primitive write to the
extension log, shown in the Extension tab in the UI::

    perceivo.log.debug(message, **fields)
    perceivo.log.info(message, **fields)
    perceivo.log.warning(message, **fields)
    perceivo.log.error(message, **fields)
    await perceivo.log.write(message, level=Level.INFO, **fields)

A component writes only the extension log, so the write path takes no
``log_type=`` parameter. Structured context is ``**fields``; passing none
sends ``null``, not ``{}``.

**Read.** One verb reads every log type::

    page = await perceivo.log.query(LogType.CONVERSATION, limit=50)

:class:`LogType` names the platform's log types; :class:`LogEntry` is what
``query`` returns.

**Conversation.** :mod:`perceivo.log.conversation` holds the conversation model
(:class:`~perceivo.log.conversation.ConversationMessage` and
:class:`~perceivo.log.conversation.ContentPart`) that agents write, policies
read and the stream plane sends. A conversation is a log that the stream
writes to.
"""

from . import conversation
from ._logger import debug, error, info, warning, write
from ._query import LogEntry, LogType, query

__all__ = [
    "LogEntry",
    "LogType",
    "conversation",
    "debug",
    "error",
    "info",
    "query",
    "warning",
    "write",
]
