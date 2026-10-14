"""Per-node environment and secret configuration for local execution."""

from __future__ import annotations

import logging
from typing import Any

from ..local.context import LocalExtensionManager
from .loader import load_manifest
from .types import NodeShell

logger = logging.getLogger("perceivo.runner.config")

# The only config keys this module reads. A unit test scans this module's AST
# and fails if a reader is added without listing its key here.
RECOGNISED_CONFIG_KEYS = frozenset(
    {"input_parameters", "secret_bindings", "forward_input_to_states"}
)

# Mainflow hop keys: consumed by the graph compiler before the runner sees the
# node, so they are recognised here but not read.
MAINFLOW_HOP_CONFIG_KEYS = frozenset(
    {"mainflow_target_node_ref", "mainflow_target_node_id", "mainflow_target_node_index"}
)

# Manifest keys that may carry a declared secret's name, in the platform's
# priority order.
_SECRET_NAME_KEYS = ("name", "alias", "key")


def declared_secret_name(entry: Any) -> str:
    """Return the declared name for one ``required_secrets`` entry, or ``""``.

    Matches the platform's rule: a plain string is the name; a mapping names it
    under ``name``, ``alias`` or ``key``, in that priority order.
    """
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in _SECRET_NAME_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def configure_ext_from_manifest(
    ext: LocalExtensionManager,
    manifest: dict[str, Any],
) -> tuple[Any, Any]:
    """Configure the LocalExtensionManager from a manifest dict."""
    declarations = {}
    values = {}
    for param in manifest.get("input_parameters", []):
        if isinstance(param, dict):
            name = param.get("name", "")
            declarations[name] = param
            if "default" in param:
                values[name] = param["default"]
    env_token = ext.set_node_env(values=values, declarations=declarations)

    declarations_s = {}
    aliases = {}
    for secret in manifest.get("required_secrets", []):
        name = declared_secret_name(secret)
        if not name:
            # Skip an entry with no name, as the platform validator does, and log it.
            # Log only the entry's key names: a malformed entry may hold a secret value.
            shape = (
                f"keys {sorted(map(str, secret))}"
                if isinstance(secret, dict)
                else f"a {type(secret).__name__}"
            )
            # Spelled out literally rather than joined from ``_SECRET_NAME_KEYS`` so no
            # secret-classified value reaches the log call.
            logger.warning(
                "Ignoring a required_secrets entry with no name/alias/key (%s)",
                shape,
            )
            continue
        declarations_s[name] = secret if isinstance(secret, dict) else {"name": name}
        # In local mode, alias maps to the same name (read from .env)
        aliases[name] = name
    secret_token = ext.set_node_secret_info(declarations=declarations_s, aliases=aliases)
    return env_token, secret_token


def configure_ext_from_shell(
    ext: LocalExtensionManager,
    shell: NodeShell,
) -> tuple[Any | None, Any | None]:
    """Configure the LocalExtensionManager from a NodeShell."""
    env_token = None
    secret_token = None
    try:
        if shell.manifest_path:
            manifest = load_manifest(shell.manifest_path)
            env_token, secret_token = configure_ext_from_manifest(ext, manifest)

        node_config = getattr(shell, "config", {}) or {}
        if not isinstance(node_config, dict):
            raise ValueError(f"Node config for {shell.name} must be a mapping")
        # Each token is kept before the next merge runs, so a failure in a later
        # step still resets what an earlier one set.
        token = _merge_configured_values(ext, node_config)
        if env_token is None:
            env_token = token
        token = _merge_secret_bindings(ext, node_config)
        if secret_token is None:
            secret_token = token
        return env_token, secret_token
    except Exception:
        reset_ext_node_context(ext, env_token, secret_token)
        raise


# Written into node config by the web builder when it saves a graph. A graph
# exported from the builder carries them, ``extension_id`` on every node, so
# naming them "ignored" would warn on graphs that are correct.
BUILDER_CONFIG_KEYS = frozenset(
    {
        "appearance",
        "execution_mode",
        "extension_id",
        "fsm",
        "instance",
        "is_agent_tool",
        "is_agent_tool_internal",
        "is_conditional_router",
        "is_database_connector",
        "is_error_handler",
        "is_generic_end",
        "is_generic_start",
        "is_iterator",
        "is_parallel_executor",
        "is_prebuilt",
        "is_sequential_executor",
        "is_template",
        "output_parameters",
        "port_order",
        "port_sides",
        "required_secrets",
        "survey_spec",
        "template_source_id",
    }
)

# Read from node config by the hosted orchestration service. The local runner
# has no use for them, but they are not mistakes in the graph.
HOSTED_CONFIG_KEYS = frozenset(
    {
        "agent_type",
        "assignment",
        "paired_policy_asset_id",
        "parallel_execution",
        "required_secrets",
        "sub_bundle",
        "survey_spec",
    }
)

PLATFORM_CONFIG_KEYS = BUILDER_CONFIG_KEYS | HOSTED_CONFIG_KEYS


def _warn_unrecognised_config_keys(node_config: dict[str, Any]) -> None:
    """Warn about node-config keys nothing in the SDK reads.

    This module reads three keys of a node's ``config`` block:
    ``input_parameters`` and ``forward_input_to_states``
    (:func:`_merge_configured_values`) and ``secret_bindings``
    (:func:`_merge_secret_bindings`). ``MAINFLOW_HOP_CONFIG_KEYS`` are consumed
    earlier by the graph compiler, and ``PLATFORM_CONFIG_KEYS`` belong to the web
    builder and the hosted service, so neither is reported. Any other key is
    ignored, so a flat form such as::

        config:
          temperature: 0.5      # ignored

    is reported, against the form that is read::

        config:
          input_parameters:
            values:
              temperature: 0.5  # read

    This warns rather than raises, because ``config`` may carry keys for another
    reader.
    """
    known = RECOGNISED_CONFIG_KEYS | MAINFLOW_HOP_CONFIG_KEYS | PLATFORM_CONFIG_KEYS
    ignored = sorted(str(k) for k in node_config if k not in known)
    if not ignored:
        return
    logger.warning(
        "Ignoring %d unrecognised node config key(s): %s. The runner reads only "
        "%s directly; %s encode a mainflow hop already consumed while building "
        "the graph. A value meant for a component parameter belongs under "
        "input_parameters.values.",
        len(ignored),
        ", ".join(ignored),
        ", ".join(sorted(RECOGNISED_CONFIG_KEYS)),
        ", ".join(sorted(MAINFLOW_HOP_CONFIG_KEYS)),
    )


def _merge_configured_values(ext: LocalExtensionManager, node_config: dict[str, Any]) -> Any | None:
    """Overlay ``input_parameters.values`` onto the node env; the token, or ``None`` if unset."""
    _warn_unrecognised_config_keys(node_config)
    parameter_config = node_config.get("input_parameters") or {}
    configured_values = dict(parameter_config.get("values") or {})
    # The hosted service exposes this node flag to FSM scripts as a parameter,
    # where the shipped sequential-executor template reads it with
    # perceivo.params.get.
    if node_config.get("forward_input_to_states"):
        configured_values["forward_input_to_states"] = "true"
    if not configured_values:
        return None
    env_info = ext.get_node_env()
    values = dict(env_info.get("values") or {})
    values.update(configured_values)
    return ext.set_node_env(
        values=values,
        declarations=dict(env_info.get("declarations") or {}),
    )


def _merge_secret_bindings(ext: LocalExtensionManager, node_config: dict[str, Any]) -> Any | None:
    """Overlay non-null ``secret_bindings`` onto the node aliases; the token, or ``None``."""
    secret_config = node_config.get("secret_bindings") or {}
    # Drop null bindings: a portable bundle declares unbound slots as null, which
    # must not overwrite the manifest's local aliases.
    configured_aliases = {
        name: bound_id
        for name, bound_id in (secret_config.get("bindings") or {}).items()
        if bound_id
    }
    if not configured_aliases:
        return None
    secret_info = ext.get_node_secret_info()
    aliases = dict(secret_info.get("aliases") or {})
    aliases.update(configured_aliases)
    return ext.set_node_secret_info(
        declarations=dict(secret_info.get("declarations") or {}),
        aliases=aliases,
    )


def reset_ext_node_context(
    ext: LocalExtensionManager,
    env_token: Any | None,
    secret_token: Any | None,
) -> None:
    """Restore node configuration in reverse order after scoped execution."""
    if secret_token is not None:
        ext.reset_node_secret_info(secret_token)
    if env_token is not None:
        ext.reset_node_env(env_token)
