"""Secrets for component code.

Components declare required secrets in their manifest. Users bind org secrets
to declared slots in the orchestration builder. The SDK resolves by name.
"""

from typing import Optional

from ..types.records import SecretDeclaration
from .vault import get
from .vault import get_declaration as _get_declaration

__all__ = ["declaration", "get"]


async def declaration(name: str) -> Optional[SecretDeclaration]:
    """Return the manifest declaration for a secret.

    The declaration carries no secret value; read values with
    :func:`perceivo.secrets.get`.

    Args:
        name: The secret name, as declared in the component's
            ``required_secrets`` manifest section.

    Returns:
        A :class:`~perceivo.types.records.SecretDeclaration`, or ``None`` if
        the name is not declared.

    Raises:
        RuntimeError: If called outside component execution context.
    """
    return _get_declaration(name)
