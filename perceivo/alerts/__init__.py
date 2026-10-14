"""Send alerts through one of two delivery routes.

    await perceivo.alerts.send(key, title=..., message=...)       admin-routed
    await perceivo.alerts.send_direct(medium, recipient, credentials_secret, message)
                                                                    component-routed

**``send`` is the default.** The component names an event *key*; the platform
routes it to whatever channels the organization's admin configured (Slack,
Teams, PagerDuty, Discord, Telegram, webhook) and to the members who
subscribed. The component cannot see or widen that audience.

**``send_direct`` is for a destination only the run knows**, such as the
Telegram chat that started the run. The component names the medium, the
recipient and the org secret holding the credential, and the message goes out
through the egress broker.

Both return one :class:`AlertDelivery`; its ``route`` field says which path
produced it. This plane only emits. Listing, acknowledging and resolving
alerts is the REST client's ``alerts`` resource.
"""

from __future__ import annotations

from ._client import send
from ._direct import send_direct
from ._types import AlertDelivery

__all__ = ["AlertDelivery", "send", "send_direct"]
