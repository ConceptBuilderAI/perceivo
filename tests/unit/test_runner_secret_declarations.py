"""Read a manifest's ``required_secrets`` the way the platform does.

The platform's component validator names a declared secret from ``name`` /
``alias`` / ``key`` on a dict entry, or from a plain string. The runner read only ``name`` with a ``""`` default, so a
manifest written ``- alias: openai_api_key`` -- which the platform validates
happily -- produced the alias map ``{"": ""}``: no usable binding, a misleading
"Bound secrets: " error naming nothing, and an empty-string declaration that also
suppressed the notebook-mode fallback.

Four components in the shipped integration corpus declare secrets this way (the
shared Embedding component, used by rag-loader, rag-consumer, rag-chatbot and
collection-loader), so the divergence was reachable from four bundles.
"""

from __future__ import annotations

import logging

import pytest

from perceivo.runner._configuration import declared_secret_name


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"name": "openai_api_key"}, "openai_api_key"),
        ({"alias": "openai_api_key"}, "openai_api_key"),
        ({"key": "openai_api_key"}, "openai_api_key"),
        ("openai_api_key", "openai_api_key"),
        # Priority is name > alias > key, matching the platform's order.
        ({"name": "winner", "alias": "loser", "key": "loser"}, "winner"),
        ({"alias": "winner", "key": "loser"}, "winner"),
        # Whitespace-only is not a name, and neither is a non-string.
        ({"name": "   ", "alias": "real"}, "real"),
        ("  padded  ", "padded"),
        ({"name": 7, "alias": "real"}, "real"),
    ],
)
def test_every_spelling_the_platform_accepts_resolves(entry, expected):
    assert declared_secret_name(entry) == expected


@pytest.mark.parametrize(
    "entry",
    [{}, {"hint": "no name at all"}, {"name": ""}, "", "   ", None, 7, []],
)
def test_an_unnameable_entry_yields_empty_not_a_bogus_name(entry):
    assert declared_secret_name(entry) == ""


def test_an_alias_declared_secret_is_bound_under_its_real_name(monkeypatch):
    """The end a caller feels: the alias map.

    Asserting on ``declared_secret_name`` alone would not catch
    ``configure_ext_from_manifest`` failing to call anything like it.
    """
    from perceivo.runner import _configuration

    captured: dict = {}

    class _Ext:
        def set_node_env(self, **kw):
            return "env-token"

        def set_node_secret_info(self, **kw):
            captured.update(kw)
            return "secret-token"

    manifest = {
        "required_secrets": [
            {"alias": "openai_api_key", "hint": "used for the embeddings endpoint"},
            {"name": "other_key"},
        ]
    }
    _configuration.configure_ext_from_manifest(_Ext(), manifest)

    assert captured["aliases"] == {
        "openai_api_key": "openai_api_key",
        "other_key": "other_key",
    }
    assert "" not in captured["aliases"]
    assert "" not in captured["declarations"]
    # The hint survives, so the declaration is the manifest entry, not a stub.
    assert captured["declarations"]["openai_api_key"]["hint"].startswith("used for")


#: A value that must never reach a log. Distinctive enough that a substring
#: search for it cannot collide with anything else the record carries.
SENTINEL = "sk-live-DO-NOT-LOG-9f3a1c"


def test_a_nameless_entry_is_skipped_and_logged_without_its_contents(caplog):
    """Skip, not raise -- the platform ignores these, and matching it is the point.

    Raising would be stricter than the server and reintroduce the divergence from
    the other side: a manifest the platform accepts would fail locally.

    The warning names the entry's *shape* and never its contents. A manifest
    declares which secret is required and does not carry the value -- but this
    branch is reached only by entries malformed in some way nobody anticipated,
    which is precisely where an inline credential ends up. Logging ``%r`` of the
    entry would put that credential into the run log in clear text (CodeQL
    py/clear-text-logging-sensitive-data). The key names are the entire diagnostic anyway: they say which spelling the
    author used and which one was expected.

    caplog is the right instrument for that claim precisely because it bypasses
    formatter-level redaction -- it sees what the call site actually passed, so
    a leak cannot be hidden behind a formatter that happens to strip it.
    """
    from perceivo.runner import _configuration

    captured: dict = {}

    class _Ext:
        def set_node_env(self, **kw):
            return "env-token"

        def set_node_secret_info(self, **kw):
            captured.update(kw)
            return "secret-token"

    manifest = {
        "required_secrets": [
            {"hint": "typo, no name", "value": SENTINEL},
            {"name": "good"},
        ]
    }
    with caplog.at_level(logging.WARNING, logger="perceivo.runner.config"):
        _configuration.configure_ext_from_manifest(_Ext(), manifest)

    assert captured["aliases"] == {"good": "good"}
    # Assert on the rendered output: a formatter-level %s is not applied to
    # caplog.record.msg, so checking the template would pass while the operator
    # saw nothing useful.
    assert "required_secrets" in caplog.text
    # The keys are named -- that is what tells the author which spelling to fix.
    assert "hint" in caplog.text
    assert "value" in caplog.text
    # No value is. Both of these are values, and one of them is a credential.
    assert SENTINEL not in caplog.text
    assert "typo, no name" not in caplog.text


@pytest.mark.parametrize(
    "entry, shape",
    [
        ({"hint": SENTINEL}, "keys ['hint']"),
        ([SENTINEL], "a list"),
        ("", "a str"),
        (7, "a int"),
        (None, "a NoneType"),
    ],
)
def test_no_malformed_shape_reaches_the_log_with_its_contents(entry, shape, caplog):
    """Every shape that reaches the skip branch, not only the dict one.

    A fix that sanitized the dict case and left ``%r`` on the others would pass
    a single-shape test while still writing a credential from a list or a bare
    string.
    """
    from perceivo.runner import _configuration

    class _Ext:
        def set_node_env(self, **kw):
            return "env-token"

        def set_node_secret_info(self, **kw):
            return "secret-token"

    with caplog.at_level(logging.WARNING, logger="perceivo.runner.config"):
        _configuration.configure_ext_from_manifest(_Ext(), {"required_secrets": [entry]})

    assert shape in caplog.text
    assert SENTINEL not in caplog.text


def test_a_bare_string_declaration_binds():
    """The platform accepts ``required_secrets: [my_key]``; so must the runner."""
    from perceivo.runner import _configuration

    captured: dict = {}

    class _Ext:
        def set_node_env(self, **kw):
            return "env-token"

        def set_node_secret_info(self, **kw):
            captured.update(kw)
            return "secret-token"

    _configuration.configure_ext_from_manifest(_Ext(), {"required_secrets": ["my_key"]})
    assert captured["aliases"] == {"my_key": "my_key"}
    assert captured["declarations"]["my_key"] == {"name": "my_key"}


def test_warning_lists_every_accepted_key(caplog):
    """The skip warning must name every spelling the platform accepts.

    The message spells the list out literally instead of interpolating
    ``_SECRET_NAME_KEYS``, because a name-based CodeQL rule flags any
    ``*SECRET*`` identifier reaching a log call. Two copies of the same list
    drift, so this reads the constant and checks the *rendered* message against
    it: add a fourth accepted key and this fails until the message says so.

    Asserting on ``caplog.text`` rather than on the template is deliberate --
    the template would satisfy a substring check while the operator, who reads
    the formatted line, saw an out-of-date list.
    """
    from perceivo.runner import _configuration

    class _Ext:
        def set_node_env(self, **kw):
            return "env-token"

        def set_node_secret_info(self, **kw):
            return "secret-token"

    with caplog.at_level(logging.WARNING, logger="perceivo.runner.config"):
        _configuration.configure_ext_from_manifest(_Ext(), {"required_secrets": [{}]})

    assert caplog.text, "the skip branch logged nothing"
    for key in _configuration._SECRET_NAME_KEYS:
        assert key in caplog.text, f"the warning never names the accepted key {key!r}"
