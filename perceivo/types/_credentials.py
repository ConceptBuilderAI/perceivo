"""The credential a public-API call authenticates with, in one of three modes.

Build one with a constructor, never the class directly::

    Credentials.api_key("pk_...")
    Credentials.hmac(api_key="pk_...", key_id="0f8f...", signing_secret="<64 hex>")
    Credentials.entra("<tenant id>", "<client id>", client_secret="...")
    Credentials.entra("<tenant id>", "<client id>", federated_token_file="/var/run/token")

Passing one to a call decides the mode on its own: environment variables and
``perceivo.yaml`` are then not read for credentials at all. Without one, the SDK
resolves exactly one mode from ``PERCEIVO_*`` environment variables and the
non-secret keys of ``perceivo.yaml``'s ``api:`` section, and refuses a mix.

``audience`` (HMAC) and ``scope`` (Entra) may be left ``None``: they default to the
production values when the base URL is ``https://api.perceivo.ai``, and are
required for any other URL.

The ``repr`` masks every secret. None of the values is ever logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ConfigurationError

__all__ = ["Credentials"]

_MASK = "<redacted>"


def _required(value: str | None, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Credentials: {what} must be a non-empty string")
    return value.strip()


def _optional(value: str | None, what: str) -> str | None:
    return None if value is None else _required(value, what)


#: mode -> (fields that must be set, fields that may be set). Every other field
#: must be ``None``, so one object can never carry two modes' credentials.
_SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "api_key": (("key",), ()),
    "hmac": (("key", "key_id", "signing_secret"), ("audience",)),
    "entra": (
        ("tenant_id", "client_id"),
        ("client_secret", "federated_token_file", "scope"),
    ),
}
_ALL_FIELDS = (
    "key",
    "key_id",
    "signing_secret",
    "audience",
    "tenant_id",
    "client_id",
    "client_secret",
    "federated_token_file",
    "scope",
)


@dataclass(frozen=True)
class Credentials:
    """A public-API credential. Construct it with :meth:`api_key`, :meth:`hmac` or
    :meth:`entra`.

    Attributes:
        mode: ``"api_key"``, ``"hmac"`` or ``"entra"``.

    Example::

        import os

        from perceivo.orchestration import publish
        from perceivo.types import Credentials

        credentials = Credentials.api_key(os.environ["PERCEIVO_API_KEY"])
        await publish("./my-orchestration", credentials=credentials)
    """

    mode: str
    key: str | None = field(default=None, repr=False)
    key_id: str | None = None
    signing_secret: str | None = field(default=None, repr=False)
    audience: str | None = None
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    federated_token_file: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        """Hold the constructors' guarantees for a direct ``Credentials(...)`` too."""
        shape = _SHAPES.get(self.mode) if isinstance(self.mode, str) else None
        if shape is None:
            raise ConfigurationError(
                f"Credentials: mode must be one of {', '.join(_SHAPES)}; build one with "
                "Credentials.api_key(), .hmac() or .entra()"
            )
        required, optional = shape
        for name in required:
            _required(getattr(self, name), name)
        for name in optional:
            _optional(getattr(self, name), name)
        allowed = required + optional
        foreign = [n for n in _ALL_FIELDS if n not in allowed and getattr(self, n) is not None]
        if foreign:
            raise ConfigurationError(
                f"Credentials: {', '.join(foreign)} cannot be set in {self.mode} mode"
            )
        if self.mode == "entra" and (self.client_secret is None) == (
            self.federated_token_file is None
        ):
            raise ConfigurationError(
                "Credentials.entra: pass exactly one of client_secret and federated_token_file"
            )

    @classmethod
    def api_key(cls, key: str) -> Credentials:
        """Authenticate with a service-account API key (``X-API-Key``)."""
        return cls(mode="api_key", key=_required(key, "the API key"))

    @classmethod
    def hmac(
        cls,
        api_key: str,
        key_id: str,
        signing_secret: str,
        audience: str | None = None,
    ) -> Credentials:
        """Authenticate with an API key whose requests are HMAC-signed.

        Args:
            api_key: The service-account API key.
            key_id: The key's id (a UUID), shown when its signing secret was enrolled.
            signing_secret: The 64-hex signing secret, shown once at enrolment.
            audience: The deployment's signing audience. Defaults to
                ``perceivo-api-prod`` for ``https://api.perceivo.ai`` only.
        """
        return cls(
            mode="hmac",
            key=_required(api_key, "the API key"),
            key_id=_required(key_id, "key_id"),
            signing_secret=_required(signing_secret, "signing_secret"),
            audience=_optional(audience, "audience"),
        )

    @classmethod
    def entra(
        cls,
        tenant_id: str,
        client_id: str,
        *,
        client_secret: str | None = None,
        federated_token_file: str | None = None,
        scope: str | None = None,
    ) -> Credentials:
        """Authenticate as a Microsoft Entra ID service principal (``Bearer`` token).

        Exactly one of ``client_secret`` and ``federated_token_file`` is required.
        The token file is re-read on every token request, because it is rotated.

        Args:
            tenant_id: The directory (tenant) id.
            client_id: The application (client) id.
            client_secret: A client secret of the app registration.
            federated_token_file: A file holding a federated (workload-identity) token.
            scope: The token scope. Defaults to the platform's scope for
                ``https://api.perceivo.ai`` only.
        """
        if (client_secret is None) == (federated_token_file is None):
            raise ConfigurationError(
                "Credentials.entra: pass exactly one of client_secret and federated_token_file"
            )
        return cls(
            mode="entra",
            tenant_id=_required(tenant_id, "tenant_id"),
            client_id=_required(client_id, "client_id"),
            client_secret=_optional(client_secret, "client_secret"),
            federated_token_file=_optional(federated_token_file, "federated_token_file"),
            scope=_optional(scope, "scope"),
        )

    def __repr__(self) -> str:
        parts = [f"mode={self.mode!r}"]
        for name in ("key", "signing_secret", "client_secret"):
            if getattr(self, name) is not None:
                parts.append(f"{name}={_MASK}")
        for name in (
            "key_id",
            "audience",
            "tenant_id",
            "client_id",
            "federated_token_file",
            "scope",
        ):
            value = getattr(self, name)
            if value is not None:
                parts.append(f"{name}={value!r}")
        return f"Credentials({', '.join(parts)})"

    __str__ = __repr__
