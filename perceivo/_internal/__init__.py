"""Internal module for the perceivo SDK; not part of the public API.

Component code may not import it: direct imports from ``perceivo._internal``
are blocked in the component sandbox. The platform's runtime services import
``_ExecutionContext`` from here to install their extension manager.
"""

from .auth import MissingCredentialsError
from .context import _ExecutionContext

__all__ = [
    "MissingCredentialsError",
    "_ExecutionContext",
]
