# tests/unit/test_alerts_rejects.py

"""What `perceivo.alerts.send` refuses, and that it refuses it *before* dispatch.

The other half of `test_alerts_send.py`. Every case here asserts `ext.send_alert.assert_not_awaited()` as well as the
raise: a guard that rejected the call *after* sending it would satisfy the
`pytest.raises` alone, and the whole point of validating in the SDK is that the
bad call never leaves the component.

`raise_on_error` does not suppress these. A malformed argument is a bug in the
component, not a delivery outcome -- unlike a suppression, which is a 200 with a
reason and is tested next door.
"""

import json

import pytest

import perceivo
from tests.unit.fixtures_alerts import _patch_ext, _stub_ext


class TestRejectsBadInputBeforeDispatch:
    """Reject locally rather than paying a round trip to learn the same thing.

    Every case here asserts `assert_not_awaited` as well as the raise: a check
    that rejected the call *after* sending it would pass on the exception alone.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key",
        [
            "invoice-overdue",  # the hyphen — the spelling a reader reaches for
            "Invoice_Overdue",  # uppercase
            "1nvoice",  # leading digit
            "i",  # one character; the grammar wants two
            "",
            "   ",
            None,
            "a" * 65,
            "invoice overdue",  # space
            "invoice.overdue",  # dot
        ],
    )
    async def test_key_grammar(self, key):
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError, match="Invalid alert key"):
            await perceivo.alerts.send(key, title="T", message="M")
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_hyphen_rejection_points_at_the_underscore(self):
        """The whole value of rejecting locally is the message.

        A 422 from two services away says the pattern did not match. This has to
        say what to type instead, because the hyphen is the mistake everyone
        makes exactly once.
        """
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError) as excinfo:
            await perceivo.alerts.send("invoice-overdue", title="T", message="M")
        assert "underscore" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["ab", "a1", "a_", "inventory_low", "a" * 64])
    async def test_the_grammar_admits_what_it_should(self, key):
        """The control beside the zeros above.

        Ten rejections prove nothing on their own -- a pattern that matched
        nothing at all would pass every one of them.
        """
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send(key, title="T", message="M")
        assert ext.send_alert.await_args.kwargs["key"] == key

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"title": ""}, "title is required"),
            ({"title": "   "}, "title is required"),
            ({"message": ""}, "message is required"),
            ({"message": "   "}, "message is required"),
            ({"title": "T" * 201}, "the maximum is 200"),
            ({"message": "M" * 2001}, "the maximum is 2000"),
        ],
    )
    async def test_required_and_bounded_fields(self, kwargs, expected):
        base = {"title": "T", "message": "M"}
        base.update(kwargs)
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError, match=expected):
            await perceivo.alerts.send("inventory_low", **base)
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("length,field", [(200, "title"), (2000, "message")])
    async def test_the_caps_are_inclusive(self, length, field):
        """200 and 2000 are allowed; 201 and 2001 are not.

        `CustomAlertIngest` uses `max_length`, which is inclusive. An off-by-one
        here would reject a call the platform would have accepted -- a failure
        only reachable at exactly the boundary, so it is tested at exactly the
        boundary.
        """
        base = {"title": "T", "message": "M", field: "x" * length}
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("inventory_low", **base)
        ext.send_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_malformed_call_raises_even_with_raise_on_error_False(self):
        """`raise_on_error` is about delivery, not about argument validation.

        Suppressing a ValueError here would mean a typo'd key returns a
        result-shaped object for an alert that was never attempted.
        """
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError):
            await perceivo.alerts.send(
                "invoice-overdue", title="T", message="M", raise_on_error=False
            )
        ext.send_alert.assert_not_awaited()


class TestRejectionsAreCatchableBothWays:
    """`AlertArgumentError` must keep both of its bases.

    The rejection tests above all catch `ValueError`, so they would keep passing
    if the `PerceivoError` base were dropped -- and the SDK's contract, "catch
    `PerceivoError` and you catch everything the SDK raises", would quietly
    become false for this plane. The converse is just as silent: dropping `ValueError` would break
    every `except ValueError` a caller already wrote against `send_direct` while
    every test here still went green. So assert both directions explicitly.
    """

    @pytest.mark.asyncio
    async def test_a_rejection_is_a_perceivo_error(self):
        from perceivo.types.errors import PerceivoError

        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(PerceivoError):
            await perceivo.alerts.send("invoice-overdue", title="T", message="M")
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rejection_is_still_a_value_error(self):
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError):
            await perceivo.alerts.send("invoice-overdue", title="T", message="M")
        ext.send_alert.assert_not_awaited()

    def test_the_error_is_not_exported_from_the_plane(self):
        # The plane's public names are the two sends and their receipt; the
        # argument error is reachable through `except ValueError` and
        # `except PerceivoError` without being named.
        assert perceivo.alerts.__all__ == ["AlertDelivery", "send", "send_direct"]
        assert not hasattr(perceivo.alerts, "AlertArgumentError")


class TestMetadataBound:
    """`metadata` is promised "up to 8 KB" in the public docstring.

    The boundary cases below compute the encoded size and assert it, rather than
    trusting the arithmetic: a test that meant to sit on the limit but landed one
    byte under would pass while proving nothing.
    """

    @pytest.mark.asyncio
    async def test_at_the_limit_is_accepted(self):
        from perceivo.alerts._client import _MAX_METADATA_BYTES

        # {"k":"<pad>"} encodes to 8 + len(pad) bytes compact.
        meta = {"k": "x" * (_MAX_METADATA_BYTES - 8)}
        assert len(json.dumps(meta, separators=(",", ":")).encode("utf-8")) == _MAX_METADATA_BYTES

        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("inventory_low", title="T", message="M", metadata=meta)
        # The control for the rejection below: exactly at the limit still goes.
        ext.send_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_byte_over_is_rejected_before_dispatch(self):
        from perceivo.alerts._client import _MAX_METADATA_BYTES

        meta = {"k": "x" * (_MAX_METADATA_BYTES - 7)}
        size = len(json.dumps(meta, separators=(",", ":")).encode("utf-8"))
        assert size == _MAX_METADATA_BYTES + 1

        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError) as exc:
            await perceivo.alerts.send("inventory_low", title="T", message="M", metadata=meta)
        # Name the measured size and the limit, so the caller can see by how much.
        assert str(size) in str(exc.value)
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multibyte_is_measured_in_bytes_not_characters(self):
        """The limit is UTF-8 **bytes**.

        A string of 3-byte characters that is comfortably under the limit by
        character count is over it by byte count. Measuring with `len()` on the
        str would let this through.
        """
        from perceivo.alerts._client import _MAX_METADATA_BYTES

        # 3000 CJK characters. ``json.dumps`` leaves ``ensure_ascii`` at its
        # default of True, so each one is emitted as the six ASCII bytes
        # ``\u4e2d`` rather than its three UTF-8 bytes -- 18008 bytes, not the
        # 9008 a raw-UTF-8 reading predicts. Either way it is over the limit and
        # under it by character count, which is what this test is about.
        meta = {"k": "中" * 3000}
        assert len(meta["k"]) < _MAX_METADATA_BYTES  # under by CHARACTERS
        assert (
            len(json.dumps(meta, separators=(",", ":")).encode("utf-8")) > _MAX_METADATA_BYTES
        )  # over by BYTES

        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError):
            await perceivo.alerts.send("inventory_low", title="T", message="M", metadata=meta)
        ext.send_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unserializable_metadata_is_named_not_raised_raw(self):
        ext = _stub_ext()
        with _patch_ext(ext), pytest.raises(ValueError) as exc:
            await perceivo.alerts.send(
                "inventory_low", title="T", message="M", metadata={"k": object()}
            )
        assert "metadata" in str(exc.value)
        ext.send_alert.assert_not_awaited()


class _LegacyExt:
    """An extension manager from before the runtime served custom alerts.

    Deliberately NOT an `AsyncMock`: a bare `AsyncMock` auto-creates every
    attribute, so `getattr(ext, "send_alert", None)` is never `None` on one and a
    test built from `_stub_ext` could not exercise this guard at all.
    """

    async def send_chat_message(self, **kwargs):  # pragma: no cover - never called
        raise AssertionError("not the method under test")


class TestRuntimeWithoutSendAlert:
    """The SDK ships ahead of the runtimes, so this window is real, not theoretical.

    A runtime that does not serve `send_alert` makes a bare call raise
    `AttributeError`, which is **not** a `PerceivoError` -- so the SDK's
    contract, "catch `PerceivoError` and you catch everything the SDK raises",
    would be false on the most likely failure path
    this module has.
    """

    @pytest.mark.asyncio
    async def test_it_names_the_runtime_and_the_missing_method(self):
        with _patch_ext(_LegacyExt()), pytest.raises(NotImplementedError) as exc:
            await perceivo.alerts.send("inventory_low", title="T", message="M")
        msg = str(exc.value)
        assert "send_alert" in msg
        assert "_LegacyExt" in msg  # names the runtime, not just the symbol

    @pytest.mark.asyncio
    async def test_a_runtime_that_serves_it_is_unaffected(self):
        # Control. Without this, the test above would still pass if the guard
        # rejected *every* runtime.
        ext = _stub_ext()
        with _patch_ext(ext):
            await perceivo.alerts.send("inventory_low", title="T", message="M")
        ext.send_alert.assert_awaited_once()
