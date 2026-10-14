"""Define the typed error hierarchy rooted at one base.

Catch ``PerceivoError`` to catch every declared SDK exception.

Bare ``ValueError``, ``RuntimeError`` and ``KeyError`` raised inline in the
SDK are not rooted on ``PerceivoError``.
"""

from __future__ import annotations

__all__ = [
    "AccessDeniedError",
    "AuthenticationError",
    "ConfigurationError",
    "ContextError",
    "NotFoundError",
    "PerceivoError",
    "ServiceError",
]


class PerceivoError(Exception):
    """Base for every error the SDK raises."""


class ConfigurationError(PerceivoError):
    """A declared secret is unbound, or a param is undeclared.

    The defect is in what the component declared, so it is not retryable and
    fails the same way on every attempt.
    """


class NotFoundError(PerceivoError):
    """The asset, instance or collection does not exist."""


class AuthenticationError(PerceivoError):
    """The public API did not accept the credential that was presented.

    Raised for a 401 from the Perceivo API, and for an Entra ID token request the
    identity platform refused (no Perceivo call is made then). It says which of the
    three authentication modes was in use and what to change, because "401" alone
    reads the same for a wrong key, a wrong signing secret and a missing consent.

    Attributes:
        mode: ``"api_key"``, ``"hmac"`` or ``"entra"``.
        status_code: The HTTP status that was answered.
        reason: The wire ``reason`` (or ``error``) value, or the ``AADSTS`` code.
        remedy: What to change. Never contains a credential.

    Example::

        from perceivo.orchestration import publish
        from perceivo.types import AuthenticationError

        try:
            await publish("./my-orchestration")
        except AuthenticationError as err:
            print(err.mode, err.reason, err.remedy)
    """

    def __init__(
        self,
        message: str = "",
        *,
        mode: str,
        status_code: int | None = None,
        reason: str | None = None,
        remedy: str = "",
    ) -> None:
        super().__init__(message)
        self.mode = mode
        self.status_code = status_code
        self.reason = reason
        self.remedy = remedy


class AccessDeniedError(PerceivoError):
    """The organization may not do this.

    Named so it does not shadow the builtin ``PermissionError``, which is an
    ``OSError`` about filesystem access.

    When the public API answers 403, ``mode``, ``status_code``, ``reason`` and
    ``remedy`` say which credential was refused and what to check.
    ``status_code`` is set whenever an HTTP status caused the error, including
    a 401 or 403 from the review-results route behind ``reviews.query``;
    ``mode``, ``reason`` and ``remedy`` are ``None`` whenever the error is not a
    public-API 403.
    """

    def __init__(
        self,
        message: str = "",
        *,
        mode: str | None = None,
        status_code: int | None = None,
        reason: str | None = None,
        remedy: str | None = None,
    ) -> None:
        super().__init__(message)
        self.mode = mode
        self.status_code = status_code
        self.reason = reason
        self.remedy = remedy


class ServiceError(PerceivoError):
    """The backing service failed.

    ``retryable`` is the whole reason this class is distinct from the others:
    it is the only one where trying again is a coherent response, so the
    caller needs to be told which kind it got. It defaults to ``False`` --
    an unknown failure is not assumed safe to repeat.

    ``retry_after`` is the server's ``Retry-After`` in seconds, when it sent one.
    ``mode``, ``reason`` and ``remedy`` are set when the public API answered, and
    ``None`` otherwise. The SDK never retries on its own.
    """

    def __init__(
        self,
        message: str = "",
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
        mode: str | None = None,
        reason: str | None = None,
        remedy: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.mode = mode
        self.reason = reason
        self.remedy = remedy

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({str(self)!r}, retryable={self.retryable}, "
            f"status_code={self.status_code})"
        )


class ContextError(PerceivoError):
    """Called outside an execution context, or in a runtime where this plane has no meaning.

    Distinct from ``ConfigurationError`` because nothing is misdeclared: the
    same call is correct inside a run and meaningless outside one.
    """
