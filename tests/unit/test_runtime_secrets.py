"""T2: perceivo.secrets access (async)."""

from __future__ import annotations

import pytest

from perceivo import secrets


class _SecretInfo:
    def __init__(self, declarations=None, aliases=None, values=None):
        self._info = {
            "declarations": declarations or {},
            "aliases": aliases or {},
        }
        self._values = values or {}

    def get_node_secret_info(self):
        return self._info

    async def fetch_secret_value(self, secret_id):
        return self._values.get(secret_id)


async def test_secrets_get_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        await secrets.get("any")


async def test_secrets_get_bound_alias_returns_value(stub_ext):
    info = _SecretInfo(
        declarations={"api_key": {"required": True}},
        aliases={"api_key": "sec_id_1"},
        values={"sec_id_1": "sk-real"},
    )
    stub_ext.get_node_secret_info = info.get_node_secret_info
    stub_ext.fetch_secret_value = info.fetch_secret_value
    assert await secrets.get("api_key") == "sk-real"


async def test_secrets_get_unbound_optional_returns_caller_default(stub_ext):
    info = _SecretInfo(
        declarations={"api_key": {"required": False}},
        aliases={},
    )
    stub_ext.get_node_secret_info = info.get_node_secret_info
    stub_ext.fetch_secret_value = info.fetch_secret_value
    assert await secrets.get("api_key", "fallback") == "fallback"


async def test_secrets_get_unbound_optional_no_default_returns_none(stub_ext):
    """Optional secret without explicit default= should return None, not raise."""
    info = _SecretInfo(
        declarations={"api_key": {"required": False}},
        aliases={},
    )
    stub_ext.get_node_secret_info = info.get_node_secret_info
    stub_ext.fetch_secret_value = info.fetch_secret_value
    assert await secrets.get("api_key") is None


async def test_secrets_get_unbound_required_raises_key_error(stub_ext):
    info = _SecretInfo(
        declarations={"api_key": {"required": True}},
        aliases={},
    )
    stub_ext.get_node_secret_info = info.get_node_secret_info
    stub_ext.fetch_secret_value = info.fetch_secret_value
    with pytest.raises(KeyError, match="not bound"):
        await secrets.get("api_key")


async def test_secrets_get_notebook_mode_treats_name_as_uuid(stub_ext):
    info = _SecretInfo(values={"some-uuid": "raw-value"})
    stub_ext.get_node_secret_info = info.get_node_secret_info
    stub_ext.fetch_secret_value = info.fetch_secret_value
    assert await secrets.get("some-uuid") == "raw-value"


async def test_secrets_declaration_returns_dict_or_none(stub_ext):
    info = _SecretInfo(declarations={"k": {"label": "K"}})
    stub_ext.get_node_secret_info = info.get_node_secret_info
    decl = await secrets.declaration("k")
    assert decl.name == "k"
    assert decl.label == "K"
    assert await secrets.declaration("missing") is None


def test_the_plane_exports_exactly_what_the_design_declares():
    """The plane declares ``get`` and ``declaration``. Nothing else ships.

    A positive assertion on both halves. ``keys`` and ``get_declaration`` are
    not on the plane, so this fails if either is added to it -- and it fails just as loudly if a
    declared member is dropped, which set equality is what buys.

    ``get_declaration`` still exists in :mod:`perceivo.secrets.vault` as the
    implementation ``declaration`` forwards to; what must not come back is the
    *plane attribute*, which is the only reach a component has.
    """
    assert set(secrets.__all__) == {"get", "declaration"}
    assert not hasattr(secrets, "keys")
    assert not hasattr(secrets, "get_declaration")

    from perceivo.secrets import vault

    assert not hasattr(vault, "keys"), (
        "vault.keys() had no caller once the plane stopped exporting it; "
        "leaving the implementation behind is dead code, not a private API."
    )
    assert callable(vault.get_declaration)


class TestTheUnboundSecretMessageNamesTheRightFault:
    """The KeyError has to say which of three different faults happened.

    A portable bundle declares a secret slot and embeds no org secret id, so the
    alias arrives as ``{"slack_bot_token": None}``. A "Bound secrets:" list
    built from ``aliases.keys()`` would list the very secret it reports as
    missing -- ``Secret 'slack_bot_token' is not bound... Bound secrets:
    slack_bot_token``. That reads as a platform fault (the binding is right
    there) rather than as the operator action it is (nothing was ever bound to
    the slot), and it sends debugging into the resolver instead of into the
    builder.
    """

    async def test_a_null_alias_is_reported_as_declared_but_unbound(self, stub_ext):
        info = _SecretInfo(
            declarations={"slack_bot_token": {"required": True}},
            aliases={"slack_bot_token": None, "openai_api_key": "sec_1"},
            values={"sec_1": "sk-real"},
        )
        stub_ext.get_node_secret_info = info.get_node_secret_info
        stub_ext.fetch_secret_value = info.fetch_secret_value

        with pytest.raises(KeyError) as exc:
            await secrets.get("slack_bot_token")
        message = str(exc.value)

        assert "declared on this node but not bound" in message
        assert "Declared but unbound: slack_bot_token" in message
        # The regression itself: the missing secret must not appear in the list
        # of working ones. Asserting on the whole message would pass while the
        # name sat in the Bound list, so anchor on the segment.
        bound_segment = message.split("Bound: ", 1)[1].split(". Declared", 1)[0]
        assert "slack_bot_token" not in bound_segment
        # A genuinely bound sibling still gets listed -- that is the diagnostic.
        assert "openai_api_key" in bound_segment

    async def test_an_undeclared_name_says_so_rather_than_blaming_a_binding(self, stub_ext):
        """A typo'd name is a different fault from an unbound slot.

        Telling the author to bind ``opena_api_key`` in the builder is advice
        that cannot work; nothing declares it.
        """
        info = _SecretInfo(
            declarations={"openai_api_key": {"required": True}},
            aliases={"openai_api_key": "sec_1"},
            values={"sec_1": "sk-real"},
        )
        stub_ext.get_node_secret_info = info.get_node_secret_info
        stub_ext.fetch_secret_value = info.fetch_secret_value

        with pytest.raises(KeyError) as exc:
            await secrets.get("opena_api_key")
        assert "not declared on this node either" in str(exc.value)

    async def test_a_bound_id_that_resolves_to_nothing_is_not_called_unbound(self, stub_ext):
        """The binding is correct and the *value* is empty -- an org-side fault.

        Reporting this as "not bound" sends the operator to re-bind a slot that
        is already bound, which changes nothing.
        """
        info = _SecretInfo(
            declarations={"api_key": {"required": True}},
            aliases={"api_key": "sec_1"},
            values={},  # the id resolves, the vault holds nothing behind it
        )
        stub_ext.get_node_secret_info = info.get_node_secret_info
        stub_ext.fetch_secret_value = info.fetch_secret_value

        with pytest.raises(KeyError) as exc:
            await secrets.get("api_key")
        message = str(exc.value)
        assert "bound but the platform returned no value" in message
        assert "not bound" not in message

    async def test_a_manifest_declaration_with_no_alias_at_all_is_unbound(self, stub_ext):
        """``unbound`` is drawn from declarations too, not only from aliases.

        A node whose manifest declares a secret the bundle never aliased has an
        empty alias map. Building the unbound list from ``aliases`` alone would
        report "not declared on this node either" about a secret the manifest
        declares in so many words.
        """
        info = _SecretInfo(declarations={"api_key": {"required": True}}, aliases={})
        stub_ext.get_node_secret_info = info.get_node_secret_info
        stub_ext.fetch_secret_value = info.fetch_secret_value

        with pytest.raises(KeyError) as exc:
            await secrets.get("api_key")
        message = str(exc.value)
        assert "declared on this node but not bound" in message
        assert "Declared but unbound: api_key" in message

    async def test_a_null_alias_still_falls_through_to_an_explicit_default(self, stub_ext):
        """The message change must not disturb the resolution path above it.

        ``default=`` is checked before the message is built, so a caller that
        supplies one never sees any of this.
        """
        info = _SecretInfo(
            declarations={"slack_bot_token": {"required": True}},
            aliases={"slack_bot_token": None},
        )
        stub_ext.get_node_secret_info = info.get_node_secret_info
        stub_ext.fetch_secret_value = info.fetch_secret_value
        assert await secrets.get("slack_bot_token", "fallback") == "fallback"
