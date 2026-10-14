"""Admin-routed alert delivery: ``perceivo.alerts.send`` over ``ext.send_alert``."""

import json
import re
from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ..types.enums import Severity
from ..types.errors import PerceivoError
from ._types import AlertDelivery, _admin_delivery


class AlertArgumentError(PerceivoError, ValueError):
    """A ``send`` argument is malformed; raised before anything goes out.

    Subclasses both ``PerceivoError`` and ``ValueError``, so either ``except``
    form catches it.
    """


# Alert key grammar enforced by the platform. Hyphens are not allowed:
# ``invoice-overdue`` is a 422, ``invoice_overdue`` is valid.
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

_VALID_SEVERITIES = ("info", "warning", "critical")

# Field limits enforced server-side, checked here so the error names the field.
_MAX_TITLE = 200
_MAX_MESSAGE = 2000

# Largest ``metadata`` object, as compact-separator UTF-8 JSON bytes. The
# measurement must match the server's exactly or the two disagree at the boundary.
_MAX_METADATA_BYTES = 8 * 1024


def _validated_key(key: str) -> str:
    """Return *key* if it matches the alert key grammar, else raise."""
    normalized_key = str(key or "").strip()
    if not _KEY_PATTERN.match(normalized_key):
        raise AlertArgumentError(
            f"Invalid alert key {key!r}. Keys match ^[a-z][a-z0-9_]{{1,63}}$ - "
            "lowercase letters, digits and underscores, starting with a letter, "
            "2 to 64 characters. Hyphens are not permitted; use underscores."
        )
    return normalized_key


def _check_text_fields(title: str, message: str) -> None:
    """Required-and-bounded on the two free-text fields.

    Lengths are measured on the value as supplied, not on the stripped form, so
    this bound is the one ``CustomAlertIngest``'s ``max_length`` applies
    server-side. Both are inclusive.
    """
    if not title or not str(title).strip():
        raise AlertArgumentError("title is required and must be a non-empty string.")
    if len(title) > _MAX_TITLE:
        raise AlertArgumentError(f"title is {len(title)} characters; the maximum is {_MAX_TITLE}.")
    if not message or not str(message).strip():
        raise AlertArgumentError("message is required and must be a non-empty string.")
    if len(message) > _MAX_MESSAGE:
        raise AlertArgumentError(
            f"message is {len(message)} characters; the maximum is {_MAX_MESSAGE}."
        )


def _normalized_severity(severity: Optional[Severity]) -> Optional[str]:
    """Return the normalized severity, passing ``None`` through untouched.

    ``None`` means the key's registered ``default_severity`` decides, so it must
    not be defaulted here. Only a supplied value is normalized and checked.
    """
    if severity is None:
        return None
    normalized = str(severity).strip().lower()
    if normalized not in _VALID_SEVERITIES:
        raise AlertArgumentError(
            f"Unsupported severity {severity!r}. "
            f"Expected one of: {', '.join(_VALID_SEVERITIES)}, or None to use "
            "the severity registered against this key."
        )
    return normalized


def _check_metadata(metadata: Optional[dict[str, Any]]) -> None:
    """Raise if *metadata* is not JSON-serializable or exceeds the size limit.

    The size is measured as compact UTF-8 JSON bytes.
    """
    if metadata is None:
        return
    try:
        encoded = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AlertArgumentError(f"metadata must be a JSON-serializable object; {exc}") from exc
    if len(encoded) > _MAX_METADATA_BYTES:
        raise AlertArgumentError(
            f"metadata is {len(encoded)} bytes; the maximum is {_MAX_METADATA_BYTES}."
        )


def _resolve_forward(ext: Any) -> Any:
    """Return the runtime's ``send_alert``, or raise a named error if it has none.

    A runtime whose extension manager does not serve ``send_alert`` raises
    :class:`NotImplementedError` naming the fix, rather than a bare
    ``AttributeError`` that is not a ``PerceivoError``.
    """
    forward = getattr(ext, "send_alert", None)
    if forward is None:
        raise NotImplementedError(
            f"perceivo.alerts.send() needs `send_alert` on the runtime's "
            f"extension manager, and {type(ext).__name__} does not have it. "
            "Use a runtime version that supports custom alerts."
        )
    return forward


async def send(
    key: str,
    *,
    title: str,
    message: str,
    severity: Optional[Severity] = None,
    metadata: Optional[dict[str, Any]] = None,
    raise_on_error: bool = True,
) -> AlertDelivery:
    """Raise a named alert; the organization's admin routes it.

    The admin-routed form, and the default. When the component itself must
    choose the destination, use :func:`perceivo.alerts.send_direct`.

    The alert **key** is the whole addressing model. Component code never names
    a recipient, a phone number or an email address: it names an event, and the
    platform resolves the audience to current members of the emitting
    organization. Membership is checked at dispatch time, so losing membership
    stops delivery without anyone editing a subscription.

    A suppression is not an error. A disabled key, an ``info`` severity or no
    alert created all return ``delivered=False`` with a ``suppressed_reason`` and
    do not raise under either ``raise_on_error`` setting.

    Args:
        key: The alert key, matching ``^[a-z][a-z0-9_]{1,63}$``: lowercase,
            digits and underscores, starting with a letter. **Hyphens are not
            permitted**; ``invoice-overdue`` is a 422 and ``invoice_overdue`` is
            the spelling that works. An unregistered key is auto-registered for
            the organization, up to 200 keys.
        title: Short headline, 1-200 characters.
        message: Body text, 1-2000 characters.
        severity: A :class:`~perceivo.types.Severity` (``INFO``, ``WARNING`` or
            ``CRITICAL``). It is a ``StrEnum``, so the bare strings ``"info"``,
            ``"warning"`` and ``"critical"`` are accepted unchanged.

            **Defaults to ``None``, which is not the same as ``WARNING``.**
            ``None`` means *"use the severity registered against this key"*, the
            ``default_severity`` the organization set when it created the key.

            ``info`` notifies nobody: it is recorded but creates no alert, and
            comes back as ``suppressed_reason="info_severity"`` rather than as an
            error.
        metadata: Optional JSON object carried with the alert, up to 8 KB.
        raise_on_error: Raise when the platform rejects or fails the emit.
            Defaults to True so an alert that did **not** go out cannot read as
            one that did. Set it to False when the alert is advisory and the run
            should continue regardless, and then read the returned
            :class:`~perceivo.alerts.AlertDelivery` to find out what happened.

    Returns:
        An :class:`~perceivo.alerts.AlertDelivery` with ``route="admin"``,
        carrying ``event_id``, ``key``, ``delivered`` and ``suppressed_reason``.
        It carries nothing about recipients.

    Raises:
        ValueError: The key does not match the grammar, a required field is
            empty or over length, or the severity is not one this platform
            dispatches. Raised as ``AlertArgumentError`` before any call goes
            out; ``raise_on_error`` does not suppress it.
        RuntimeError: The emit failed or was rejected, and ``raise_on_error`` is
            True.
        NotImplementedError: This runtime's extension manager does not serve
            ``send_alert``.

    Example::

        await perceivo.alerts.send(
            "inventory_low",
            title="Stock below threshold",
            message="SKU 123 at 4 units",
            severity="warning",
        )
    """
    normalized_key = _validated_key(key)
    _check_text_fields(title, message)
    normalized_severity = _normalized_severity(severity)
    _check_metadata(metadata)

    forward = _resolve_forward(_ExecutionContext.get_ext())
    result = await forward(
        key=normalized_key,
        title=title,
        message=message,
        severity=normalized_severity,
        metadata=metadata,
        raise_on_error=raise_on_error,
    )
    return _admin_delivery(result)
