# tests/unit/fixtures_alerts.py

"""Shared stubs for the two `perceivo.alerts.send` test modules.

Split out when `test_alerts_send.py` reached 504 lines. The rejection cases
moved to `test_alerts_rejects.py`; these two helpers are what both halves need, and duplicating them would let the
two halves drift into stubbing different things.
"""

from unittest.mock import AsyncMock, patch


def _stub_ext(result=None, error=None):
    ext = AsyncMock()
    if error is not None:
        ext.send_alert.side_effect = error
    else:
        ext.send_alert.return_value = result or {
            "event_id": "evt-1",
            "key": "inventory_low",
            "delivered": True,
        }
    return ext


def _patch_ext(ext):
    return patch("perceivo.alerts._client._ExecutionContext.get_ext", return_value=ext)
