"""Input parameter access for component code.

Input parameters are declared in the component manifest's `input_parameters`
section. Values are configured in the node explorer when building an
orchestration, saved with the node config, and injected by the orchestration
manager at runtime.

The SDK returns typed values based on the manifest declaration:
    - text / text_long  -> str
    - number            -> float
    - integer           -> int
    - boolean           -> bool
    - select            -> str
    - multi_select      -> List[str]
    - secret            -> str (resolved from org secrets at runtime)
"""

import re
from collections.abc import Callable
from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ..types.errors import ConfigurationError
from ..types.records import ParamDeclaration, _from_wire


# Sentinel for distinguishing "no default provided" from "default is None"
class _Sentinel:
    def __repr__(self) -> str:
        return "<no default>"


_SENTINEL = _Sentinel()


_TRUE_WORDS = frozenset({"true", "1", "yes"})
_FALSE_WORDS = frozenset({"false", "0", "no"})


def _parse_bool(value: Any) -> bool:
    """Read a boolean from a bool, a 0 or 1, or a true or false word.

    The words are true, 1 and yes, and false, 0 and no, in any case and with
    surrounding whitespace ignored.

    Raises:
        ValueError: Any other value, which would otherwise read as one or the other
            by accident.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE_WORDS | _FALSE_WORDS:
        return value.strip().lower() in _TRUE_WORDS
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise ValueError(f"not a boolean: {value!r}")


def _parse_list(value: Any) -> list[str]:
    """Parse a list from various input formats."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value)]


_TYPE_COERCIONS: dict[str, Callable[[Any], Any]] = {
    "text": str,
    "text_long": str,
    "number": float,
    "integer": int,
    "boolean": _parse_bool,
    "select": str,
    "multi_select": _parse_list,
    "secret": str,
}


# A number or integer may be given as a reference to a field of the incoming
# data, such as "${chunk_size}", which the component fills in itself. The
# platform forwards number and integer references unread, so they are returned
# unchanged rather than refused as unreadable. A boolean reference still raises:
# the platform reads a boolean node value holding one as False and returns a
# boolean default holding one as the str.
_REFERENCE = re.compile(r"\$\{[^}]+\}")


def _coerce(name: str, declared: dict[str, Any], value: Any) -> Any:
    declared_type = declared.get("type", "text")
    if (
        declared_type in ("number", "integer")
        and isinstance(value, str)
        and _REFERENCE.search(value)
    ):
        return value
    coerce = _TYPE_COERCIONS.get(declared_type, str)
    try:
        return coerce(value)
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"Input parameter '{name}' is declared as '{declared_type}', and its "
            f"{type(value).__name__} value cannot be read as one."
        ) from exc


# The node editor writes "" for a cleared number or integer field, and the
# platform stores and forwards node values without checking them against the
# declared type, so "" can arrive for any of these.
_EMPTY_IS_UNSET = frozenset({"boolean", "number", "integer"})


def _is_unset(declared: dict[str, Any], value: Any) -> bool:
    return declared.get("type") in _EMPTY_IS_UNSET and value == ""


def get(name: str, default: Any = _SENTINEL) -> Any:
    """
    Get an input parameter value by name.

    Returns a typed value based on the parameter's manifest declaration.
    If the parameter was declared as a number, returns float. If boolean,
    returns bool. If multi_select, returns List[str]. And so on.

    Args:
        name: The parameter name as declared in the manifest.
        default: Fallback value if the parameter is not set and has no
                 manifest default. If omitted and the parameter is missing,
                 raises KeyError.

    Returns:
        The parameter value, coerced to the declared type. A manifest default is
        coerced the same way; a default declared as ``null`` is returned as
        ``None``. A boolean, number or integer whose value, or manifest
        default, is ``""`` is not set. A number or integer value or manifest
        default that holds a ``${field}`` reference, such as
        ``"${chunk_size}"``, is returned unchanged as a str: the component
        resolves the reference itself from the incoming data. A boolean
        holding one raises.

    Raises:
        ConfigurationError: The value, or the manifest default, cannot be read
            as the declared type. The message names the parameter and the type.
        KeyError: If the parameter doesn't exist and no default is provided.
        RuntimeError: If called outside of component execution context.

    Example:
        ```python
        import perceivo

        class MyAgent(perceivo.primitives.Agent):
            async def run(self, input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
                provider = perceivo.params.get("provider")
                temperature = perceivo.params.get("temperature", 0.7)
                stream = perceivo.params.get("stream", False)
                formats = perceivo.params.get("output_format")  # List[str]
        ```
    """
    ext = _ExecutionContext.get_ext()
    env_data = ext.get_node_env()

    values = env_data.get("values", {})
    declarations = env_data.get("declarations", {})

    if name in values:
        raw_value = values[name]
        if name not in declarations:
            return raw_value
        if not _is_unset(declarations[name], raw_value):
            return _coerce(name, declarations[name], raw_value)

    if name in declarations:
        decl = declarations[name]
        if "default" in decl and not _is_unset(decl, decl["default"]):
            if decl["default"] is None:
                return None
            return _coerce(name, decl, decl["default"])

    if default is not _SENTINEL:
        return default

    available = ", ".join(sorted(values.keys())) if values else "(none)"
    raise KeyError(
        f"Input parameter '{name}' is not set and has no default. Available parameters: {available}"
    )


def keys() -> list[str]:
    """
    List all available input parameter names.

    Returns parameter names from both configured values and manifest
    declarations (some may have defaults but no explicit value set).

    Returns:
        Sorted list of parameter names.

    Example:
        ```python
        all_params = perceivo.params.keys()
        for name in all_params:
            value = perceivo.params.get(name, None)
            perceivo.log.info(f"{name} = {value}")
        ```
    """
    ext = _ExecutionContext.get_ext()
    env_data = ext.get_node_env()

    all_keys = set(env_data.get("values", {}).keys())
    all_keys.update(env_data.get("declarations", {}).keys())
    return sorted(all_keys)


def declaration(name: str) -> Optional[ParamDeclaration]:
    """Get the full manifest declaration for an input parameter.

    Returns the parameter definition as declared in the component's
    manifest, including type, label, options, constraints, defaults,
    and dependency rules. Keys the record does not name are kept in
    ``decl.extra``.

    An absent ``default`` and a ``default`` declared as ``null`` both read as
    ``decl.default is None``, although :func:`get` treats them differently; do
    not use ``decl.default is None`` to decide whether a default was declared.

    Args:
        name: The parameter name.

    Returns:
        A :class:`~perceivo.types.records.ParamDeclaration`, or ``None`` if the
        parameter has no manifest declaration.

    Example:
        ```python
        decl = perceivo.params.declaration("model")
        if decl and decl.depends_on:
            parent = decl.depends_on["parameter"]
            perceivo.log.info(f"Model options depend on {parent}")
        ```
    """
    ext = _ExecutionContext.get_ext()
    env_data = ext.get_node_env()
    declarations = env_data.get("declarations", {})
    raw = declarations.get(name)
    if raw is None:
        return None
    # Set ``name`` from the key; the platform does not write it into every value.
    return _from_wire(ParamDeclaration, {"name": name, **raw}, collect_extra=True)


def all() -> dict[str, Any]:
    """
    Get all input parameter values as a dict.

    Returns all configured values, coerced to their declared types.
    Parameters with manifest defaults but no explicit value are included.

    Returns:
        Dict mapping parameter name to typed value.

    Raises:
        ConfigurationError: A value or manifest default cannot be read as its
            declared type.

    Example:
        ```python
        config = perceivo.params.all()
        perceivo.log.info(f"Running with config: {config}")
        ```
    """
    all_values = {}
    for name in keys():
        try:
            all_values[name] = get(name)
        except KeyError:
            pass
    return all_values
