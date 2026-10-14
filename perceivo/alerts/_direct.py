"""Component-routed alert delivery: ``perceivo.alerts.send_direct`` over ``ext.send_chat_message``."""

from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ..types.enums import Severity
from ._types import AlertDelivery, _direct_delivery

_VALID_MEDIA = ("telegram", "slack", "discord", "teams")
_VALID_SEVERITIES = ("info", "warning", "critical")

# The platform's failure status for a dispatch.
_FAILED_STATUS = "failed"


def _raise_if_undelivered(result: Any, medium: str) -> None:
    """Raise the ``RuntimeError`` :func:`send_direct` documents if *result* failed.

    An ext with no HTTP route in front of it (local adapters, mocks) returns a
    failed dispatch as an ordinary result with ``status: "failed"``. This turns it
    into an error. ``delivery_id`` cannot tell success from failure: it is minted
    before the provider is called. An unrecognised status passes through.

    The recipient is never included in the error text: for Slack, Telegram and
    Teams it is itself an org secret.
    """
    if not isinstance(result, dict):
        return
    if str(result.get("status") or "").strip().lower() != _FAILED_STATUS:
        return
    detail = str(result.get("error") or "").strip() or "no error detail was returned"
    raise RuntimeError(
        # ASCII only: this text is printed on cp1252 Windows consoles.
        f"Direct alert to {medium} failed - the message was NOT delivered: {detail}"
    )


async def send_direct(
    medium: str,
    recipient: str,
    credentials_secret: str,
    message: str,
    *,
    title: Optional[str] = None,
    action_url: Optional[str] = None,
    action_label: Optional[str] = None,
    severity: Severity = Severity.INFO,
    fields: Optional[dict[str, str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AlertDelivery:
    """Send an alert to a destination this component chooses.

    The component-routed form of :func:`perceivo.alerts.send`. Use it when the
    recipient is decided at run time, such as the Telegram chat that started the
    run. When the audience is "whoever the organization subscribed", use
    :func:`perceivo.alerts.send`, which the org admin routes.

    Routes through the egress broker, so the target host must be an approved
    outbound route for the organization (Infrastructure > Networking > Outbound).
    A message that is not delivered raises rather than reporting success; catch
    the error at the call site if the run should continue.

    Args:
        medium: One of ``telegram``, ``slack``, ``discord``, ``teams``.
        recipient: Chat id, channel id, or webhook URL, whichever the platform
            addresses by. Slack takes a channel id, Telegram a chat id, Teams a
            webhook URL.
        credentials_secret: The NAME of the org secret holding the platform
            credential, exactly as declared in the component's
            ``required_secrets``. **Not** a secret id, and not the value: the
            runtime resolves the name internally, so a component script never
            handles either.
        message: Message body text.
        title: Optional bold header.
        action_url: Optional button/link URL.
        action_label: Optional button text.
        severity: A :class:`~perceivo.types.Severity` (``INFO``, ``WARNING`` or
            ``CRITICAL``). It is a ``StrEnum``, so the bare strings ``"info"``,
            ``"warning"`` and ``"critical"`` are accepted unchanged.
        fields: Optional key-value pairs rendered as metadata.
        metadata: Optional metadata for audit logging.

    Returns:
        An :class:`~perceivo.alerts.AlertDelivery` with ``route="direct"`` and
        ``delivery_id``, ``medium``, ``status`` and ``timestamp`` set.

    Raises:
        ValueError: The medium or severity is not one this platform dispatches.
        RuntimeError: ``credentials_secret`` is not declared in the component's
            manifest or not bound on the node, or the dispatch itself failed.

    Example::

        await perceivo.alerts.send_direct(
            medium="slack",
            recipient="C0123456789",
            credentials_secret="slack_bot_token",
            message="Nightly load finished: 57 documents.",
            title="Collection Loader",
            severity="info",
            fields={"documents": "57", "duration": "42s"},
        )
    """
    normalized = str(medium or "").strip().lower()
    if normalized not in _VALID_MEDIA:
        raise ValueError(
            f"Unsupported medium {medium!r}. Expected one of: {', '.join(_VALID_MEDIA)}."
        )

    normalized_severity = str(severity or "info").strip().lower()
    if normalized_severity not in _VALID_SEVERITIES:
        raise ValueError(
            f"Unsupported severity {severity!r}. Expected one of: {', '.join(_VALID_SEVERITIES)}."
        )

    if not recipient:
        raise ValueError("recipient is required — a chat id, channel id, or webhook URL.")
    if not credentials_secret:
        raise ValueError(
            "credentials_secret is required — the NAME of the org secret holding "
            "the platform credential, as declared in required_secrets."
        )
    if not message:
        raise ValueError("message is required and must be a non-empty string.")

    ext = _ExecutionContext.get_ext()
    result = await ext.send_chat_message(
        channel=normalized,
        recipient=recipient,
        credentials_secret=credentials_secret,
        message=message,
        title=title,
        action_url=action_url,
        action_label=action_label,
        severity=normalized_severity,
        fields=fields,
        metadata=metadata,
    )
    _raise_if_undelivered(result, normalized)
    return _direct_delivery(result)
