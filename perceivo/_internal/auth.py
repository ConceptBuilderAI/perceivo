"""Resolve the one authentication mode and base URL a public-API call uses.

Three modes, selected by configuration and never by guessing:

* ``api_key``: ``X-API-Key``.
* ``hmac``: ``X-API-Key`` plus a v1 request signature over the exact bytes sent.
* ``entra``: a Microsoft Entra ID bearer token for a bound service principal.

**An explicit** ``credentials=`` **wins wholesale.** It alone decides the mode;
credential fields in the environment and ``perceivo.yaml`` are not read.

**Otherwise the environment and** ``perceivo.yaml`` **merge field by field**, the
environment winning. ``perceivo.yaml`` may hold non-secret fields only.

**The merged set must name exactly one mode.** Anything else raises
:class:`ConfigurationError` naming every variable involved and where it came from.

The base URL resolves independently, from operator-controlled sources only: the
argument (or CLI ``--base-url``), then ``PERCEIVO_API_URL``, then
``https://api.perceivo.ai``. No configuration file is a source: the base URL decides
where the credential is sent, and a discovered file may not be the operator's, so a
file naming it is refused outright (see :mod:`.config_discovery`). ``http://`` is
refused except for a loopback host, and a path, query or fragment is refused.
There is no fallback to ``AZURE_*`` variables and no reading of ``.env``.
"""

from __future__ import annotations

import ipaddress
import os
import urllib.parse
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import cast

from ..types._credentials import Credentials
from ..types.errors import ConfigurationError
from .config_discovery import ApiConfig, discover_config, read_api_config

__all__ = [
    "DEFAULT_API_URL",
    "AuthMode",
    "MissingCredentialsError",
    "ResolvedAuth",
    "resolve",
]

DEFAULT_API_URL = "https://api.perceivo.ai"
PROD_HMAC_AUDIENCE = "perceivo-api-prod"
PROD_TOKEN_SCOPE = "api://bf151286-9548-4e40-af09-48523cdfc3e7/.default"


class AuthMode(StrEnum):
    API_KEY = "api_key"
    HMAC = "hmac"
    ENTRA = "entra"


class MissingCredentialsError(ConfigurationError, RuntimeError):
    """No credential was found in the arguments, the environment or ``perceivo.yaml``."""


@dataclass(frozen=True)
class ResolvedAuth:
    """Exactly one mode, a complete credential for it, and the base URL."""

    base_url: str
    mode: AuthMode
    credentials: Credentials


#: credential field -> (environment variable, perceivo.yaml ``api:`` key or None)
_FIELDS: dict[str, tuple[str, str | None]] = {
    "key": ("PERCEIVO_API_KEY", None),
    "key_id": ("PERCEIVO_KEY_ID", "key_id"),
    "signing_secret": ("PERCEIVO_SIGNING_SECRET", None),
    "audience": ("PERCEIVO_SIGNING_AUDIENCE", "signing_audience"),
    "tenant_id": ("PERCEIVO_TENANT_ID", "tenant_id"),
    "client_id": ("PERCEIVO_CLIENT_ID", "client_id"),
    "client_secret": ("PERCEIVO_CLIENT_SECRET", None),
    "federated_token_file": ("PERCEIVO_FEDERATED_TOKEN_FILE", "federated_token_file"),
    "scope": ("PERCEIVO_TOKEN_SCOPE", "token_scope"),
}
_HMAC_FIELDS = ("key_id", "signing_secret")
_ENTRA_FIELDS = ("tenant_id", "client_id", "client_secret", "federated_token_file")
_LOOPBACK_NAMES = {"localhost"}


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _check_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname or parsed.scheme not in ("https", "http"):
        raise ConfigurationError(f"the API base URL {url!r} is not an http(s) URL")
    if parsed.query or parsed.fragment:
        raise ConfigurationError(f"the API base URL {url!r} must not carry a query or fragment")
    if parsed.path not in ("", "/"):
        raise ConfigurationError(
            f"the API base URL {url!r} must not carry a path: request paths such as "
            "/v1/orchestrations/import are fixed by the SDK and signed as sent. Use the "
            "scheme and host only, for example " + DEFAULT_API_URL
        )
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ConfigurationError(
            f"the API base URL {url!r} uses http://, which would send the credential in "
            "clear. Use https:// (http:// is accepted for a loopback host only)."
        )
    return url.rstrip("/")


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url(explicit: str | None) -> tuple[str, str]:
    """Return ``(url, "NAME [source]")`` from operator-controlled sources only.

    No :class:`ApiConfig` is taken: a configuration file never names the URL.
    """
    if explicit:
        return _check_url(explicit), "base_url=/--base-url [argument]"
    from_env = _env("PERCEIVO_API_URL")
    if from_env is not None:
        return _check_url(from_env), "PERCEIVO_API_URL [env]"
    return DEFAULT_API_URL, "the default"


def _complete(creds: Credentials, base_url: str, url_source: str) -> Credentials:
    """Fill the production defaults, or refuse when the URL is not production."""
    is_prod = base_url == DEFAULT_API_URL
    if creds.mode == AuthMode.HMAC and creds.audience is None:
        if not is_prod:
            raise ConfigurationError(
                f"HMAC signing against {base_url} (from {url_source}) needs an audience: set "
                "PERCEIVO_SIGNING_AUDIENCE (or api.signing_audience, or "
                "Credentials.hmac(audience=...)). It defaults only for " + DEFAULT_API_URL
            )
        return cast(Credentials, replace(creds, audience=PROD_HMAC_AUDIENCE))  # type: ignore[redundant-cast]
    if creds.mode == AuthMode.ENTRA and creds.scope is None:
        if not is_prod:
            raise ConfigurationError(
                f"Entra ID against {base_url} (from {url_source}) needs a token scope: set "
                "PERCEIVO_TOKEN_SCOPE "
                "(or api.token_scope, or Credentials.entra(scope=...)). It defaults only for "
                + DEFAULT_API_URL
            )
        return cast(Credentials, replace(creds, scope=PROD_TOKEN_SCOPE))  # type: ignore[redundant-cast]
    return creds


def _merged(api: ApiConfig, yaml_source: str) -> dict[str, tuple[str, str]]:
    """Return ``field -> (value, "NAME [source]")`` for every field that is set."""
    found: dict[str, tuple[str, str]] = {}
    for field, (env_name, yaml_key) in _FIELDS.items():
        env_value = _env(env_name)
        if env_value is not None:
            found[field] = (env_value, f"{env_name} [env]")
            continue
        yaml_value = getattr(api, yaml_key) if yaml_key else None
        if yaml_value is not None:
            found[field] = (yaml_value, f"api.{yaml_key} [{yaml_source}]")
    return found


def _refuse(problem: str, found: dict[str, tuple[str, str]], names: tuple[str, ...]) -> None:
    involved = ", ".join(found[name][1] for name in names if name in found)
    raise ConfigurationError(f"{problem}: {involved}")


def _value(found: dict[str, tuple[str, str]], name: str) -> str | None:
    return found[name][0] if name in found else None


def _refuse_hmac_conflicts(found: dict[str, tuple[str, str]]) -> None:
    if ("key_id" in found) != ("signing_secret" in found):
        _refuse(
            "HMAC signing needs both PERCEIVO_KEY_ID and PERCEIVO_SIGNING_SECRET",
            found,
            _HMAC_FIELDS,
        )
    if "key_id" in found and "key" not in found:
        _refuse("HMAC signing also needs PERCEIVO_API_KEY", found, _HMAC_FIELDS)


def _refuse_entra_conflicts(found: dict[str, tuple[str, str]], entra: tuple[str, ...]) -> None:
    if ("tenant_id" in found) != ("client_id" in found):
        _refuse("Entra ID needs both PERCEIVO_TENANT_ID and PERCEIVO_CLIENT_ID", found, entra)
    if "tenant_id" not in found:
        _refuse("Entra ID settings without PERCEIVO_TENANT_ID and PERCEIVO_CLIENT_ID", found, entra)
    secret, token_file = "client_secret" in found, "federated_token_file" in found
    if secret and token_file:
        _refuse(
            "Entra ID takes a client secret or a federated token file, not both",
            found,
            ("client_secret", "federated_token_file"),
        )
    if not (secret or token_file):
        _refuse(
            "Entra ID needs PERCEIVO_CLIENT_SECRET or PERCEIVO_FEDERATED_TOKEN_FILE", found, entra
        )


def _from_layers(found: dict[str, tuple[str, str]]) -> Credentials:
    """Resolve the single mode named by the merged environment and perceivo.yaml fields."""
    entra = tuple(name for name in _ENTRA_FIELDS if name in found)
    if "key" in found and entra:
        _refuse(
            "PERCEIVO_API_KEY cannot be combined with Entra ID settings", found, ("key", *entra)
        )
    _refuse_hmac_conflicts(found)
    if entra:
        _refuse_entra_conflicts(found, entra)
        return Credentials.entra(
            found["tenant_id"][0],
            found["client_id"][0],
            client_secret=_value(found, "client_secret"),
            federated_token_file=_value(found, "federated_token_file"),
            scope=_value(found, "scope"),
        )
    if "key_id" in found:
        return Credentials.hmac(
            found["key"][0],
            found["key_id"][0],
            found["signing_secret"][0],
            audience=_value(found, "audience"),
        )
    if "key" in found:
        return Credentials.api_key(found["key"][0])
    raise MissingCredentialsError(
        "No public-API credential. Set PERCEIVO_API_KEY (API key), PERCEIVO_API_KEY + "
        "PERCEIVO_KEY_ID + PERCEIVO_SIGNING_SECRET (HMAC), or PERCEIVO_TENANT_ID + "
        "PERCEIVO_CLIENT_ID + PERCEIVO_CLIENT_SECRET or PERCEIVO_FEDERATED_TOKEN_FILE "
        "(Entra ID), or pass credentials=Credentials.<mode>(...). The project's .env is "
        "read for component secrets, not for platform credentials."
    )


def _mode_problem(
    creds: Credentials, found: dict[str, tuple[str, str]], base_url: str, url_source: str
) -> None:
    """Check the non-production audience and scope fields.

    Raises:
        ConfigurationError: If the fields are incomplete; the message names the
            source each involved field was resolved from.
    """
    try:
        _complete(creds, base_url, url_source)
    except ConfigurationError as exc:
        names = _HMAC_FIELDS if creds.mode == AuthMode.HMAC else _ENTRA_FIELDS
        involved = ", ".join(found[name][1] for name in names if name in found)
        raise ConfigurationError(f"{exc} (resolved from {involved})") from None


def resolve(
    *,
    project_dir: str | os.PathLike[str] | None = None,
    credentials: Credentials | None = None,
    base_url: str | None = None,
) -> ResolvedAuth:
    """Resolve the mode, credential and base URL before any network I/O.

    Args:
        project_dir: Where ``perceivo.yaml`` discovery starts.
        credentials: An explicit credential. When given, it alone decides the mode.
        base_url: An explicit base URL. Otherwise ``PERCEIVO_API_URL``, then the
            production default. Never a configuration file.

    Raises:
        MissingCredentialsError: Nothing names a credential.
        ConfigurationError: A conflict, an incomplete mode, a secret or a base URL
            in ``perceivo.yaml``, or an ``http://`` URL that is not loopback.
    """
    config_path = discover_config(project_dir)
    api = read_api_config(config_path) if config_path else ApiConfig()
    resolved_url, url_source = _base_url(base_url)
    if credentials is not None:
        if not isinstance(credentials, Credentials):
            raise ConfigurationError("credentials must be a perceivo.types.Credentials")
        creds = _complete(credentials, resolved_url, url_source)
    else:
        found = _merged(api, f"perceivo.yaml:{Path(config_path)}" if config_path else "")
        creds = _from_layers(found)
        _mode_problem(creds, found, resolved_url, url_source)
        creds = _complete(creds, resolved_url, url_source)
    return ResolvedAuth(base_url=resolved_url, mode=AuthMode(creds.mode), credentials=creds)
