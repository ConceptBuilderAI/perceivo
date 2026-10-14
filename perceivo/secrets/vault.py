"""Secret resolution for component code.

Components declare required secrets in their manifest's `required_secrets`
section. Users bind org secrets to declared slots in the orchestration builder.
At runtime, the SDK resolves bound secrets by declared name.
"""

from typing import Any, Optional, TypeVar, cast, overload

from .._internal.context import _ExecutionContext
from ..types.records import SecretDeclaration, _from_wire


# Sentinel for distinguishing "no default provided" from "default is None"
class _Sentinel:
    def __repr__(self) -> str:
        return "<no default>"


_SENTINEL = _Sentinel()
_DefaultT = TypeVar("_DefaultT")


@overload
async def get(name: str) -> str | None: ...


@overload
async def get(name: str, default: _DefaultT) -> str | _DefaultT: ...


async def get(name: str, default: object = _SENTINEL) -> object:
    """
    Get a secret value by its declared name.

    Looks up the secret bound to the declared name via the orchestration
    builder. The actual secret value is fetched from the vault at runtime.

    Args:
        name: The secret name as declared in the component manifest's
              `required_secrets` section.
        default: Fallback value if the secret is not bound. For secrets
                 declared as `required: false`, returns this value if
                 provided, or None if omitted. For required secrets,
                 raises KeyError if not provided.

    Returns:
        The secret value as a string.

    Raises:
        KeyError: If the secret is not bound and no default is provided.
        RuntimeError: If called outside of component execution context.

    Example:
        ```python
        import perceivo
        import httpx

        class MyAgent(perceivo.primitives.Agent):
            async def run(self, input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
                api_key = await perceivo.secrets.get("anthropic_key")

                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": api_key},
                        json={...}
                    )
        ```
    """
    ext = _ExecutionContext.get_ext()
    secret_info = ext.get_node_secret_info()

    declarations = secret_info.get("declarations", {})
    aliases = secret_info.get("aliases", {})

    # A declared-but-unbound slot is present in ``aliases`` with a null id, so the
    # value, not key membership, decides whether there is a secret to fetch.
    secret_id = aliases.get(name)
    if secret_id:
        result = await ext.fetch_secret_value(secret_id)
        if result:
            return cast(str, result)

    if name in declarations:
        decl = declarations[name]
        is_required = decl.get("required", True)
        if not is_required:
            return default if default is not _SENTINEL else None

    # Notebook mode fallback: no aliases/declarations exist, so treat
    # `name` as a direct secret UUID and try fetching it.
    if not aliases and not declarations:
        result = await ext.fetch_secret_value(name)
        if result:
            return cast(str, result)

    if default is not _SENTINEL:
        return default

    raise KeyError(_missing_secret_message(name, declarations, aliases))


def _missing_secret_message(
    name: str, declarations: dict[str, Any], aliases: dict[str, Any]
) -> str:
    """The ``KeyError`` message :func:`get` raises for a secret it cannot resolve."""
    # An alias present with a null id is declared but unbound: portable bundles
    # carry the slot without an org secret id.
    bound = sorted(k for k, v in aliases.items() if v)
    unbound = sorted((set(declarations) | set(aliases)) - set(bound))

    if name in bound:
        detail = f"Secret '{name}' is bound but the platform returned no value for it"
    elif name in unbound:
        detail = f"Secret '{name}' is declared on this node but not bound to an org secret"
    else:
        detail = f"Secret '{name}' is not bound, and it is not declared on this node either"

    return (
        f"{detail}, and no default was provided. "
        f"Bound: {', '.join(bound) if bound else '(none)'}. "
        f"Declared but unbound: {', '.join(unbound) if unbound else '(none)'}."
    )


def get_declaration(name: str) -> Optional[SecretDeclaration]:
    """Get the manifest declaration for a secret.

    Returns the secret definition as declared in the component's
    `required_secrets` manifest section. A declaration never carries a value:
    any key outside ``name``, ``label``, ``description`` and ``required`` is
    dropped, so an inline ``value:`` in a manifest never reaches component code.

    Args:
        name: The secret name.

    Returns:
        A :class:`~perceivo.types.records.SecretDeclaration`, or ``None`` if
        the name is not declared. ``required`` defaults to ``True`` when the
        manifest omits it.

    Example:
        ```python
        decl = await perceivo.secrets.declaration("anthropic_key")
        if decl and decl.required:
            perceivo.log.info("anthropic_key is required")
        ```
    """
    ext = _ExecutionContext.get_ext()
    secret_info = ext.get_node_secret_info()
    declarations = secret_info.get("declarations", {})
    raw = declarations.get(name)
    if raw is None:
        return None
    return _from_wire(SecretDeclaration, {"name": name, **raw})
