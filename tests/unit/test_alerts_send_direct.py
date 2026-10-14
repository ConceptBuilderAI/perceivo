# tests/unit/test_alerts_send_direct.py

"""Tests for perceivo.alerts.send_direct, the component-routed alert route.

The backend for this already existed: the platform orchestration service's
``ExtensionManager.send_chat_message`` resolves the credential NAME to a secret
id from the node's alias map and posts through the egress broker. The SDK simply
had no public way to reach it, which is what blocked three chat samples.

These tests pin the two properties that matter:

  * the credential is passed BY NAME and never as an id or a value, so a
    component script cannot end up handling either
  * a failed dispatch RAISES rather than returning a success-shaped result
"""

from unittest.mock import AsyncMock, patch

import pytest

import perceivo


def _stub_ext(result=None, error=None):
    ext = AsyncMock()
    if error is not None:
        ext.send_chat_message.side_effect = error
    else:
        ext.send_chat_message.return_value = result or {
            "delivery_id": "d-1",
            "channel": "slack",
            "status": "sent",
            "timestamp": "2026-07-20T00:00:00Z",
        }
    return ext


class TestDispatch:
    @pytest.mark.asyncio
    async def test_passes_the_credential_by_NAME_not_by_id(self):
        """The whole point of the design: scripts never handle a secret id."""
        ext = _stub_ext()
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            await perceivo.alerts.send_direct(
                medium="slack",
                recipient="C0123456789",
                credentials_secret="slack_bot_token",
                message="hello",
            )
        kwargs = ext.send_chat_message.await_args.kwargs
        assert kwargs["credentials_secret"] == "slack_bot_token"
        # No id-shaped or value-shaped argument is introduced anywhere.
        assert "credentials_secret_id" not in kwargs
        assert "secret_value" not in kwargs

    @pytest.mark.asyncio
    async def test_returns_the_delivery_receipt(self):
        ext = _stub_ext()
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            result = await perceivo.alerts.send_direct(
                medium="slack", recipient="C1", credentials_secret="s", message="m"
            )
        assert result.delivery_id == "d-1"
        assert result.status == "sent"

    @pytest.mark.asyncio
    async def test_channel_and_severity_are_normalized(self):
        ext = _stub_ext()
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            await perceivo.alerts.send_direct(
                medium="  SLACK ",
                recipient="C1",
                credentials_secret="s",
                message="m",
                severity="WARNING",
            )
        kwargs = ext.send_chat_message.await_args.kwargs
        assert kwargs["channel"] == "slack"
        assert kwargs["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_optional_fields_are_forwarded(self):
        ext = _stub_ext()
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            await perceivo.alerts.send_direct(
                medium="teams",
                recipient="https://example.invalid/hook",
                credentials_secret="teams_webhook_url",
                message="m",
                title="T",
                action_url="https://example.invalid",
                action_label="Open",
                fields={"a": "1"},
            )
        kwargs = ext.send_chat_message.await_args.kwargs
        assert kwargs["title"] == "T"
        assert kwargs["action_label"] == "Open"
        assert kwargs["fields"] == {"a": "1"}


class TestFailsLoudly:
    @pytest.mark.asyncio
    async def test_dispatch_failure_RAISES_and_is_not_reported_as_sent(self):
        """A notification that silently did not arrive is worse than an error.

        The caller believes the message went out. If a run should survive an
        undeliverable message that must be a deliberate decision at the call
        site, not a default buried in the SDK.
        """
        ext = _stub_ext(error=RuntimeError("Chat dispatch failed: 502"))
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(RuntimeError, match="Chat dispatch failed"),
        ):
            await perceivo.alerts.send_direct(
                medium="slack", recipient="C1", credentials_secret="s", message="m"
            )

    @pytest.mark.asyncio
    async def test_a_RETURNED_failure_raises_too_not_only_a_raised_one(self):
        """The test above covers an ext that *raises*. This covers
        an ext that *returns* the platform's failure object, which was the gap.

        Only the platform's HTTP route converts ``status: "failed"`` into a 502.
        Any ext without that route in front of it — the local adapters, the mocks,
        any in-process transport — hands the failure back as an ordinary return
        value, and every caller downstream reads it as a successful send.
        """
        ext = _stub_ext(
            result={
                "delivery_id": "d-9",
                "channel": "slack",
                "status": "failed",
                "error": "telegram returned 400: chat not found",
            }
        )
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(RuntimeError, match=r"NOT delivered.*chat not found"),
        ):
            await perceivo.alerts.send_direct(
                medium="slack", recipient="C1", credentials_secret="s", message="m"
            )

    @pytest.mark.asyncio
    async def test_the_failure_message_does_not_leak_the_recipient(self):
        """For Slack, Telegram and Teams the recipient IS an org secret.

        An exception message is one of the easiest places for one to escape into
        a log, so the channel is named and the address is not.
        """
        ext = _stub_ext(result={"status": "failed", "error": "boom"})
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(RuntimeError) as excinfo,
        ):
            await perceivo.alerts.send_direct(
                medium="slack",
                recipient="https://hooks.slack.com/services/T00/B00/XXXXSECRETXXXX",
                credentials_secret="s",
                message="m",
            )
        assert "XXXXSECRETXXXX" not in str(excinfo.value)
        assert "hooks.slack.com" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_an_unrecognised_status_is_passed_through_not_guessed_at(self):
        """Only the status the platform actually reports as failure raises.

        Inventing a failure for a status the platform may add later would break
        working bundles for no evidence.
        """
        ext = _stub_ext(
            result={
                "delivery_id": "d-2",
                "channel": "slack",
                "status": "queued",
                "timestamp": "2026-07-20T00:00:00Z",
            }
        )
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            result = await perceivo.alerts.send_direct(
                medium="slack", recipient="C1", credentials_secret="s", message="m"
            )
        assert result.status == "queued"

    @pytest.mark.asyncio
    async def test_unbound_secret_raises_from_the_runtime(self):
        ext = _stub_ext(error=RuntimeError("Secret 'slack_bot_token' not found."))
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(RuntimeError, match="not found"),
        ):
            await perceivo.alerts.send_direct(
                medium="slack",
                recipient="C1",
                credentials_secret="slack_bot_token",
                message="m",
            )


class TestRejectsBadInputBeforeDispatch:
    """Reject locally rather than paying a round trip to learn the same thing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("medium", ["", "sms", "email", "Slackk", None])
    async def test_unsupported_medium(self, medium):
        ext = _stub_ext()
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(ValueError, match=r"[Uu]nsupported medium"),
        ):
            await perceivo.alerts.send_direct(
                medium=medium, recipient="C1", credentials_secret="s", message="m"
            )
        ext.send_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsupported_severity(self):
        ext = _stub_ext()
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(ValueError, match=r"[Uu]nsupported severity"),
        ):
            await perceivo.alerts.send_direct(
                medium="slack",
                recipient="C1",
                credentials_secret="s",
                message="m",
                severity="fatal",
            )
        ext.send_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"recipient": ""}, "recipient is required"),
            ({"credentials_secret": ""}, "credentials_secret is required"),
            ({"message": ""}, "message is required"),
        ],
    )
    async def test_required_arguments(self, kwargs, expected):
        base = {
            "medium": "slack",
            "recipient": "C1",
            "credentials_secret": "s",
            "message": "m",
        }
        base.update(kwargs)
        ext = _stub_ext()
        with (
            patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext),
            pytest.raises(ValueError, match=expected),
        ):
            await perceivo.alerts.send_direct(**base)
        ext.send_chat_message.assert_not_awaited()


class TestNamespace:
    def test_send_direct_lives_on_alerts_and_is_distinct_from_stream(self):
        """`perceivo.stream` is the INTERACTIVE user channel, not this.

        Direct alerts live on `perceivo.alerts`; there is no `perceivo.notify`
        plane, and the two sends share one receipt type.
        """
        assert "alerts" in perceivo.__all__
        assert "notify" not in perceivo.__all__
        assert not hasattr(perceivo, "notify")
        assert perceivo.alerts.send_direct is not perceivo.stream.send
        assert set(perceivo.alerts.__all__) == {"AlertDelivery", "send", "send_direct"}

    @pytest.mark.asyncio
    async def test_both_routes_return_one_receipt_type(self):
        ext = _stub_ext()
        with patch("perceivo.alerts._direct._ExecutionContext.get_ext", return_value=ext):
            receipt = await perceivo.alerts.send_direct(
                medium="slack", recipient="C1", credentials_secret="s", message="m"
            )
        assert isinstance(receipt, perceivo.alerts.AlertDelivery)
        assert receipt.route == "direct"
        assert receipt.delivered is True
        assert receipt.medium == "slack"
        assert receipt.event_id is None


class TestVersionConsistency:
    """The public package has one version across source and installed metadata."""

    @staticmethod
    def _versions() -> tuple[str, str, str]:
        import importlib.metadata
        import pathlib
        import tomllib

        root = pathlib.Path(__file__).resolve().parents[2]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        declared = project["project"]["version"]
        version_file = (root / "VERSION").read_text(encoding="utf-8").strip()
        installed = importlib.metadata.version("perceivo")
        return declared, version_file, installed

    def test_root_version_sources_match(self):
        declared, version_file, installed = self._versions()
        assert declared == version_file == installed, (
            "version mismatch: "
            f"pyproject.toml={declared!r}, VERSION={version_file!r}, "
            f"installed metadata={installed!r}"
        )

    def test_runtime_version_matches_installed_metadata(self):
        _declared, _version_file, installed = self._versions()
        assert perceivo.__version__ == installed
