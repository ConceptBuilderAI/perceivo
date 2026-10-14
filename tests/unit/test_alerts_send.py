"""Verify event-key alert delivery, validation and recipient privacy.

The platform selects recipients. A missing severity uses the registered default,
suppression is a successful result, and malformed calls fail before dispatch.
"""

from unittest.mock import AsyncMock

import pytest

import perceivo
from perceivo.alerts import AlertDelivery
from perceivo.types import Severity
from tests.unit.fixtures_alerts import _patch_ext, _stub_ext


class TestDispatch:
    @pytest.mark.asyncio
    async def test_the_caller_names_an_event_and_never_a_recipient(self):
        """The caller cannot select recipients.

        There is no argument through which a component can name a phone number,
        an email address or a user id -- so it cannot reach anyone the platform
        did not resolve for it. Asserting on the forwarded kwargs is the way to
        notice if one is ever added.
        """
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send(
                "inventory_low", title="Stock low", message="SKU 123 at 4 units"
            )
        kwargs = ext.send_alert.await_args.kwargs
        assert set(kwargs) == {
            "key",
            "title",
            "message",
            "severity",
            "metadata",
            "raise_on_error",
        }

    @pytest.mark.asyncio
    async def test_returns_the_alert_result(self):
        ext = _stub_ext()
        with _patch_ext(ext):
            result = await perceivo.alerts.send("inventory_low", title="T", message="M")
        assert isinstance(result, AlertDelivery)
        assert result.event_id == "evt-1"
        assert result.delivered is True
        assert result.suppressed_reason is None

    @pytest.mark.asyncio
    async def test_the_key_is_stripped_before_it_goes_out(self):
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("  inventory_low  ", title="T", message="M")
        assert ext.send_alert.await_args.kwargs["key"] == "inventory_low"

    @pytest.mark.asyncio
    async def test_metadata_and_raise_on_error_are_forwarded(self):
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send(
                "inventory_low",
                title="T",
                message="M",
                metadata={"sku": "123"},
                raise_on_error=False,
            )
        kwargs = ext.send_alert.await_args.kwargs
        assert kwargs["metadata"] == {"sku": "123"}
        assert kwargs["raise_on_error"] is False

    @pytest.mark.asyncio
    async def test_raise_on_error_defaults_to_True(self):
        """The SDK MUST NOT swallow a failure.

        `perceivo.alerts.send_direct` raises unconditionally for the same reason. An
        alert that did not go out must not read as one that did.
        """
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("inventory_low", title="T", message="M")
        assert ext.send_alert.await_args.kwargs["raise_on_error"] is True


class TestSeverityDefersToTheKey:
    """The default is `None`, and `None` is a value with meaning -- not "unset"."""

    @pytest.mark.asyncio
    async def test_omitted_severity_is_forwarded_as_None_not_as_warning(self):
        """The reconciliation against the shipped producer, pinned.

        `None` reaches `_ingest.py` and lets `body.severity or
        alert_type.default_severity` resolve. A constant would mean an
        organization that registered `disk_pressure` as `critical` sees it arrive
        as `warning`, and a subscription filtered to critical never fires.
        """
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("disk_pressure", title="T", message="M")
        assert ext.send_alert.await_args.kwargs["severity"] is None

    @pytest.mark.asyncio
    async def test_an_explicit_severity_is_normalized(self):
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send(
                "disk_pressure", title="T", message="M", severity="  CRITICAL "
            )
        assert ext.send_alert.await_args.kwargs["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_the_enum_member_is_accepted_like_the_bare_string(self):
        """`Severity` is a `StrEnum`, exactly as on `perceivo.alerts.send_direct`."""
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send(
                "disk_pressure", title="T", message="M", severity=Severity.WARNING
            )
        assert ext.send_alert.await_args.kwargs["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_an_unsupported_severity_is_rejected_before_dispatch(self):
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError, match="Unsupported severity"):
            await perceivo.alerts.send("disk_pressure", title="T", message="M", severity="fatal")
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_rejection_names_None_as_an_option(self):
        """Otherwise the error teaches the caller to pick a constant.

        Someone who hits this message is choosing a severity right now, and the
        message is the only place that tells them they do not have to.
        """
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError) as excinfo:
            await perceivo.alerts.send("disk_pressure", title="T", message="M", severity="fatal")
        assert "None" in str(excinfo.value)


class TestASuppressionIsNotAnError:
    """A 200 with a reason. `delivered=False` is an outcome, not a failure.

    `info` is a documented success that "reaches Redis/Loki but notifies nobody".
    If that raised, every informational alert would abort its run -- so `info`
    would be unusable, which is the exact opposite of what it is documented for.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", ["info_severity", "key_disabled", "no_alert_created"])
    @pytest.mark.parametrize("raise_on_error", [True, False])
    async def test_a_suppression_returns_under_either_setting(self, reason, raise_on_error):
        ext = _stub_ext(
            result={
                "event_id": "evt-2",
                "key": "nightly_summary",
                "delivered": False,
                "suppressed_reason": reason,
            }
        )
        with _patch_ext(ext):
            result = await perceivo.alerts.send(
                "nightly_summary",
                title="T",
                message="M",
                raise_on_error=raise_on_error,
            )
        assert result.delivered is False
        assert result.suppressed_reason == reason

    @pytest.mark.asyncio
    async def test_a_real_failure_still_propagates(self):
        """The runtime raises; the SDK does not catch it on the way past.

        `raise_on_error` is decided by the ext, which is the only layer that can
        see the transport. This asserts the SDK adds no `except` of its own.
        """
        ext = _stub_ext(error=RuntimeError("Alert emit failed: 502"))
        with _patch_ext(ext), pytest.raises(RuntimeError, match="Alert emit failed"):
            await perceivo.alerts.send("inventory_low", title="T", message="M")


class TestTheWireContract:
    @pytest.mark.asyncio
    async def test_suppressed_reason_defaults_when_the_platform_omits_it(self):
        """A delivered alert carries no reason, and absence must not be a fault."""
        ext = _stub_ext(result={"event_id": "e", "key": "inventory_low", "delivered": True})
        with _patch_ext(ext):
            result = await perceivo.alerts.send("inventory_low", title="T", message="M")
        assert result.suppressed_reason is None

    @pytest.mark.asyncio
    async def test_a_field_added_server_side_later_does_not_break_running_code(self):
        """`_from_wire` drops unknown keys, and that is what makes this safe."""
        ext = _stub_ext(
            result={
                "event_id": "e",
                "key": "inventory_low",
                "delivered": True,
                "a_field_invented_next_quarter": 1,
            }
        )
        with _patch_ext(ext):
            result = await perceivo.alerts.send("inventory_low", title="T", message="M")
        assert result.event_id == "e"
        assert not hasattr(result, "a_field_invented_next_quarter")

    @pytest.mark.asyncio
    async def test_a_missing_required_field_is_a_fault_not_a_default(self):
        """The backing service disagreeing with its own contract.

        Defaulting `delivered` would hand the component a plausible value nobody
        sent -- and the plausible value is `False`, which reads as "suppressed"
        for an alert that may well have gone out.
        """
        from perceivo.types import ServiceError

        ext = _stub_ext(result={"event_id": "e", "key": "inventory_low"})
        with _patch_ext(ext), pytest.raises(ServiceError):
            await perceivo.alerts.send("inventory_low", title="T", message="M")

    def test_the_result_carries_no_recipient_or_fan_out_field(self):
        """The result must not disclose the platform-selected audience.

        A `recipients` or `delivered_count` field would leak organization
        membership into component code.
        """
        from dataclasses import fields

        names = {f.name for f in fields(AlertDelivery)}
        leaking = {
            n
            for n in names
            if any(w in n for w in ("recipient", "audience", "subscriber", "count"))
        }
        assert not leaking, f"AlertDelivery discloses the audience: {sorted(leaking)}"
        assert {"event_id", "key", "delivered", "suppressed_reason"} <= names


class TestNamespace:
    def test_the_two_routes_are_distinct_functions(self):
        """Two sends that both "send something to someone" -- easy to conflate.

        They are opposites: `send_direct` takes the audience from the caller,
        `send` resolves it on the platform. If one ever became an alias of the
        other, callers could select admin-routed alert recipients.
        """
        assert "alerts" in perceivo.__all__
        assert perceivo.alerts.send is not perceivo.alerts.send_direct
        assert perceivo.alerts.__all__ == ["AlertDelivery", "send", "send_direct"]


class TestLocalMode:
    """The local adapter reports an alert as UNDELIVERED, and does not raise.

    `send_chat_message` raises in local mode because its return shape has no way
    to say "not delivered" -- a `Delivery` looks identical either way, so an
    exception is the only honest answer. `AlertDelivery.delivered` can say it, so
    the `record_cost` log-and-return pattern applies instead.
    """

    @pytest.mark.asyncio
    async def test_local_send_returns_undelivered_rather_than_raising(self):
        from perceivo.local.adapters.infra import LocalInfraStubs

        result = await LocalInfraStubs().send_alert(
            key="inventory_low", title="T", message="M", severity=None
        )
        assert result["delivered"] is False
        assert result["suppressed_reason"] == "local"
        assert result["key"] == "inventory_low"

    @pytest.mark.asyncio
    async def test_the_local_return_satisfies_the_public_contract(self):
        """Otherwise local mode exercises a shape the cloud would never send.

        The point of running under `perceivo.runner` is to find the mismatch
        before the cloud does, which only works if the stub's dict is one
        `_from_wire` accepts.
        """
        from perceivo.local.adapters.infra import LocalInfraStubs

        ext = AsyncMock()
        ext.send_alert.return_value = await LocalInfraStubs().send_alert(
            key="inventory_low", title="T", message="M"
        )
        with _patch_ext(ext):
            result = await perceivo.alerts.send("inventory_low", title="T", message="M")
        assert isinstance(result, AlertDelivery)
        assert result.delivered is False
        assert result.suppressed_reason == "local"

    @pytest.mark.asyncio
    async def test_chat_still_raises_locally(self):
        """The control beside the test above.

        If `send_chat_message` ever stopped raising, the contrast the alerts
        adapter is documented against would be gone and nothing else would say
        so.
        """
        from perceivo.local.adapters.infra import LocalInfraStubs

        # Constructed outside `pytest.raises` so the block holds exactly one
        # possibly-throwing call. Inlined, a `LocalInfraStubs.__init__` that
        # began raising `RuntimeError` would satisfy the assertion on its own
        # and this control would pass green without `send_chat_message` ever
        # being reached -- the contrast it exists to guard, silently gone
        # (python:S5778).
        stubs = LocalInfraStubs()

        with pytest.raises(RuntimeError, match=r"not.*available in local"):
            await stubs.send_chat_message(channel="slack")
