"""Which ``perceivo.yaml`` a public-API call reads, and its ``api:`` section.

**Discovery.** ``PERCEIVO_CONFIG`` when it is set; otherwise the nearest
``perceivo.yaml`` found by walking upward from the project directory, inclusive,
to the filesystem root; otherwise none. So ``perceivo publish ./proj`` run from a
parent directory reads ``proj``'s own file (or its nearest ancestor's), never the
working directory's. ``perceivo.config.load()`` keeps its own rule
(``PERCEIVO_CONFIG``, then ``./perceivo.yaml``).

**The ``api:`` section holds non-secret settings only.** ``perceivo.yaml`` is
scaffolded into the project and routinely committed, so a secret there is refused
by name, with the environment variable that carries it instead.

**No configuration file chooses the API base URL.** The base URL decides where a
credential is sent, and a discovered file may belong to someone other than the
operator: a cloned project, a template, or any ancestor directory. So
``api.url``, any alias of it, and a top-level ``api_url``/``base_url`` refuse the
whole file, naming the key and the file. The URL comes only from an explicit
argument, the CLI ``--base-url`` flag or ``PERCEIVO_API_URL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from ..types.errors import ConfigurationError

__all__ = [
    "ApiConfig",
    "build_api_config",
    "discover_config",
    "read_api_config",
    "refuse_url_keys",
]

CONFIG_FILENAME = "perceivo.yaml"

#: Names refused in ``perceivo.yaml`` because their values are secrets. Each is
#: carried by the environment variable ``PERCEIVO_<NAME>`` instead.
SECRET_KEYS = frozenset({"api_key", "signing_secret", "client_secret"})

#: Keys of the ``api:`` section that would name the API base URL. Each refuses the
#: file: the base URL is never read from a configuration file.
URL_KEYS = frozenset({"url", "base_url", "api_url", "endpoint", "host", "uri", "server"})

#: Top-level keys that would name the API base URL. Refused for the same reason.
TOP_LEVEL_URL_KEYS = frozenset({"api_url", "base_url", "perceivo_api_url"})

_URL_REMEDY = (
    "The API base URL decides where credentials are sent, so it is never read from a "
    "configuration file. Remove the key and set PERCEIVO_API_URL in the environment, "
    "pass --base-url, or pass base_url= instead."
)


@dataclass
class ApiConfig:
    """The non-secret public-API settings of ``perceivo.yaml``'s ``api:`` section.

    Every field is optional; the environment wins over each one field by field.
    """

    key_id: str | None = None
    signing_audience: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    federated_token_file: str | None = None
    token_scope: str | None = None


_API_KEYS = tuple(f.name for f in fields(ApiConfig))


def refuse_url_keys(raw: Any, *, source: str = CONFIG_FILENAME) -> None:
    """Refuse a configuration mapping that names the API base URL anywhere.

    Raises :class:`ConfigurationError` naming the key and ``source``. The value is
    never echoed.
    """
    if not isinstance(raw, dict):
        return
    for key in raw:
        if str(key).lower() in TOP_LEVEL_URL_KEYS:
            raise ConfigurationError(f"{source}: {key} is refused. {_URL_REMEDY}")
    section = raw.get("api")
    if isinstance(section, dict):
        for key in section:
            if str(key).lower() in URL_KEYS:
                raise ConfigurationError(f"{source}: api.{key} is refused. {_URL_REMEDY}")


def build_api_config(
    section: Any, *, source: str = CONFIG_FILENAME, base_dir: Path | None = None
) -> ApiConfig:
    """Validate an ``api:`` mapping.

    A secret, an unknown key or a non-string value raises
    :class:`ConfigurationError` naming it and ``source``. Every caller first passes
    the whole file to :func:`refuse_url_keys`.
    """
    if section is None:
        return ApiConfig()
    if not isinstance(section, dict):
        raise ConfigurationError(f"{source}: api must be a mapping, got {type(section).__name__}")
    for key in section:
        if key in SECRET_KEYS:
            raise ConfigurationError(
                f"{source}: api.{key} is a secret and is refused in perceivo.yaml, which is "
                f"routinely committed. Set PERCEIVO_{str(key).upper()} in the environment instead."
            )
    unknown = sorted(str(key) for key in section if key not in _API_KEYS)
    if unknown:
        raise ConfigurationError(
            f"{source}: unknown api key(s) {', '.join(unknown)}. Valid keys: {', '.join(_API_KEYS)}"
        )
    values: dict[str, str | None] = {}
    for key in _API_KEYS:
        value = section.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigurationError(f"{source}: api.{key} must be a non-empty string")
        values[key] = value.strip() if isinstance(value, str) else None
    token_file = values["federated_token_file"]
    if token_file and base_dir is not None and not Path(token_file).is_absolute():
        values["federated_token_file"] = str((base_dir / token_file).resolve())
    return ApiConfig(**values)


def discover_config(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Return the ``perceivo.yaml`` a public-API call for ``start`` reads, or ``None``."""
    named = os.environ.get("PERCEIVO_CONFIG")
    if named:
        path = Path(named)
        if not path.is_file():
            raise ConfigurationError(f"PERCEIVO_CONFIG names {named}, which is not a file")
        return path.resolve()
    directory = Path(start if start is not None else Path.cwd()).resolve()
    if directory.is_file():
        directory = directory.parent
    for candidate in (directory, *directory.parents):
        found = candidate / CONFIG_FILENAME
        if found.is_file():
            return found
    return None


def read_api_config(path: Path) -> ApiConfig:
    """Load ``path`` and return its validated ``api:`` section."""
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{CONFIG_FILENAME}:{path}: the file is not a mapping")
    source = f"{CONFIG_FILENAME}:{path}"
    refuse_url_keys(raw, source=source)
    return build_api_config(raw.get("api"), source=source, base_dir=path.parent)
