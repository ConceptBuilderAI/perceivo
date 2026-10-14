"""Define ``AlertDelivery``, the result type of both alert delivery routes.

``perceivo.alerts`` has two ways to send, and they differ in who chooses the
destination:

* :func:`perceivo.alerts.send` is **admin-routed**. The component names an
  event key; the platform resolves the audience from the organization's alert
  subscriptions and channels, which the component cannot see or widen.
* :func:`perceivo.alerts.send_direct` is **component-routed**. The component
  names the medium, the recipient and the org secret holding the credential.

Both return one :class:`AlertDelivery`. ``route`` says which path produced it,
and the fields that only one path fills are ``None`` on the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ..types.errors import ServiceError

__all__ = ["AlertDelivery"]

#: Keys each route's producer always emits. A response that omits one is the
#: backing service disagreeing with its own contract, not a legitimate subset.
_ADMIN_REQUIRED = ("event_id", "key", "delivered")
_DIRECT_REQUIRED = ("delivery_id", "channel", "status", "timestamp")


@dataclass(frozen=True)
class AlertDelivery:
    """The receipt for one alert, from either delivery route.

    **Neither route proves arrival.** ``delivered`` on the admin route means an
    alert row was created and handed to the organization's subscriptions; it
    does not say whether any subscriber matched, and deliberately carries
    nothing about recipients. On the direct route, ``send_direct`` *raises* if
    the provider rejects the message, so the only guarantee a returned receipt
    carries is that the provider accepted it.
    """

    #: ``"admin"`` for :func:`~perceivo.alerts.send`, ``"direct"`` for
    #: :func:`~perceivo.alerts.send_direct`.
    route: Literal["admin", "direct"]
    #: Admin route: an alert was created (not that anyone received it).
    #: Direct route: the provider accepted the message.
    delivered: bool

    # Admin route
    #: The alert event id.
    event_id: str | None = None
    #: The key as accepted, after the ``^[a-z][a-z0-9_]{1,63}$`` check.
    key: str | None = None
    #: Present only when ``delivered`` is False on the admin route:
    #: ``key_disabled``, ``info_severity`` or ``no_alert_created``; a local
    #: run reports ``local``.
    suppressed_reason: str | None = None
    is_trashed: bool | None = None
    trashed_at: str | None = None
    created_by_user_id: str | None = None
    updated_by_user_id: str | None = None

    # Direct route
    #: Minted by the dispatcher *before* the provider is called, so it is not
    #: evidence of delivery on its own.
    delivery_id: str | None = None
    #: ``telegram``, ``slack``, ``discord`` or ``teams``.
    medium: str | None = None
    #: The dispatcher's status string.
    status: str | None = None
    #: ISO-8601 in production; the local mock writes ``"local"``.
    timestamp: str | None = None
    #: The provider's own id: telegram ``message_id``, slack ``ts``, discord
    #: ``id``; ``None`` for teams.
    platform_message_id: str | None = None
    platform_response: dict[str, Any] | None = None
    error: str | None = None


def _require(raw: Mapping[str, Any], keys: tuple[str, ...], route: str) -> None:
    missing = [k for k in keys if k not in raw]
    if missing:
        raise ServiceError(
            f"AlertDelivery ({route} route) response is missing "
            f"{', '.join(repr(k) for k in missing)}; got {sorted(raw)}"
        )


def _admin_delivery(raw: Mapping[str, Any]) -> AlertDelivery:
    """Build the receipt from a ``CustomAlertIngestResult`` dict."""
    _require(raw, _ADMIN_REQUIRED, "admin")
    return AlertDelivery(
        route="admin",
        delivered=bool(raw["delivered"]),
        event_id=raw["event_id"],
        key=raw["key"],
        suppressed_reason=raw.get("suppressed_reason"),
        is_trashed=raw.get("is_trashed"),
        trashed_at=raw.get("trashed_at"),
        created_by_user_id=raw.get("created_by_user_id"),
        updated_by_user_id=raw.get("updated_by_user_id"),
    )


def _direct_delivery(raw: Mapping[str, Any]) -> AlertDelivery:
    """Build the receipt from a ``ChatSendResult.to_dict()`` dict."""
    _require(raw, _DIRECT_REQUIRED, "direct")
    return AlertDelivery(
        route="direct",
        # A failed dispatch raises before this is built (see ``_direct``), so
        # a receipt in hand is one the provider accepted.
        delivered=True,
        delivery_id=raw["delivery_id"],
        medium=raw["channel"],
        status=raw["status"],
        timestamp=raw["timestamp"],
        platform_message_id=raw.get("platform_message_id"),
        platform_response=raw.get("platform_response"),
        error=raw.get("error"),
    )
