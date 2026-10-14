"""Extension-configuration coverage for the runner, split out of
``test_cov_runner_cli.py``.

These tests drive ``_configure_ext_from_manifest`` and
``_configure_ext_from_shell`` -- the code that binds a manifest's input
parameters and required secrets onto a ``LocalExtensionManager`` -- plus the
``load_manifest`` guard that rejects a non-mapping manifest. The bodies are
unchanged from their original module.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from perceivo.config import load
from perceivo.local.context import LocalExtensionManager
from perceivo.runner._configuration import (
    configure_ext_from_manifest as _configure_ext_from_manifest,
)
from perceivo.runner._configuration import (
    configure_ext_from_shell as _configure_ext_from_shell,
)
from perceivo.runner.loader import load_manifest

# ---------------------------------------------------------------------------
# executor: _configure_ext_from_manifest / _configure_ext_from_shell
# ---------------------------------------------------------------------------


def test_configure_ext_from_manifest_mixed_entries():
    ext = MagicMock()
    manifest = {
        "input_parameters": [
            {"name": "with_default", "default": 5},
            {"name": "no_default"},
            "not-a-dict",
        ],
        # The two lists do NOT share a contract. An
        # input_parameters entry must be a dict, but required_secrets accepts a
        # bare string as a declaration -- see the platform's
        # `_manifest_secret_names`. Asserting that both ignore "not-a-dict"
        # would encode a divergence from the server: a manifest reading
        # `required_secrets: [bare_tok]` would validate in the cloud and bind
        # nothing locally.
        "required_secrets": [
            {"name": "tok"},
            "bare_tok",
            {"alias": "aliased_tok"},
        ],
    }
    _configure_ext_from_manifest(ext, manifest)

    _, env_kwargs = ext.set_node_env.call_args
    assert env_kwargs["values"] == {"with_default": 5}
    assert set(env_kwargs["declarations"]) == {"with_default", "no_default"}

    _, sec_kwargs = ext.set_node_secret_info.call_args
    assert sec_kwargs["declarations"] == {
        "tok": {"name": "tok"},
        "bare_tok": {"name": "bare_tok"},
        "aliased_tok": {"alias": "aliased_tok"},
    }
    assert sec_kwargs["aliases"] == {
        "tok": "tok",
        "bare_tok": "bare_tok",
        "aliased_tok": "aliased_tok",
    }


def test_configure_ext_from_shell_no_manifest_path_is_noop():
    ext = MagicMock()
    shell = SimpleNamespace(manifest_path=None)
    _configure_ext_from_shell(ext, shell)
    ext.set_node_env.assert_not_called()
    ext.set_node_secret_info.assert_not_called()


def test_configure_ext_from_shell_with_manifest(tmp_path):
    manifest_file = tmp_path / "m.yaml"
    manifest_file.write_text(
        "input_parameters:\n  - name: p\n    default: 1\nrequired_secrets: []\n",
        encoding="utf-8",
    )
    ext = MagicMock()
    shell = SimpleNamespace(manifest_path=str(manifest_file))
    _configure_ext_from_shell(ext, shell)
    ext.set_node_env.assert_called_once()


def test_configure_ext_from_shell_applies_node_overrides(tmp_path):
    manifest_file = tmp_path / "m.yaml"
    manifest_file.write_text(
        "input_parameters:\n  - name: p\n    default: 1\nrequired_secrets:\n  - name: token\n",
        encoding="utf-8",
    )
    ext = LocalExtensionManager(load(None))
    shell = SimpleNamespace(
        manifest_path=str(manifest_file),
        config={
            "input_parameters": {"values": {"p": 7}},
            "secret_bindings": {"bindings": {"token": "secret-id"}},
        },
    )

    _configure_ext_from_shell(ext, shell)

    assert ext.get_node_env()["values"] == {"p": 7}
    assert ext.get_node_secret_info()["aliases"] == {"token": "secret-id"}


def test_load_manifest_rejects_non_mapping_yaml(tmp_path):
    manifest_file = tmp_path / "m.yaml"
    manifest_file.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_manifest(str(manifest_file))


def test_configure_ext_from_shell_restores_context_when_config_is_invalid():
    ext = LocalExtensionManager(load(None))
    ext.set_node_env(values={"owner": "parent"}, declarations={})
    ext.set_node_secret_info(declarations={}, aliases={"token": "parent-secret"})
    shell = SimpleNamespace(manifest_path=None, config=["not", "a", "mapping"], name="bad")

    with pytest.raises(ValueError, match="must be a mapping"):
        _configure_ext_from_shell(ext, shell)

    assert ext.get_node_env()["values"] == {"owner": "parent"}
    assert ext.get_node_secret_info()["aliases"] == {"token": "parent-secret"}


# ---------------------------------------------------------------------------
# _configuration: unrecognised node-config keys are named, not swallowed
# ---------------------------------------------------------------------------


def _shell(config):
    return SimpleNamespace(name="node-1", manifest_path=None, config=config)


def test_a_flat_node_config_is_named_instead_of_silently_dropped(caplog):
    """The defect: ``config: {temperature: 0.5}`` configured nothing, quietly.

    The flat form reads exactly like configuration and is discarded whole. The
    run then completes with the parameter at its default and no output of any
    kind pointing at the cause, which is what makes it expensive to find.
    """
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(ext, _shell({"temperature": 0.5, "model": "x"}))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    message = warnings[0].getMessage()
    # Every ignored key by name, and the spelling that would have worked.
    assert "temperature" in message
    assert "model" in message
    assert "input_parameters.values" in message


@pytest.mark.parametrize(
    "node_config",
    [
        {1: "x"},
        {"temperature": 0.5, 2: "y"},
    ],
)
def test_a_non_string_config_key_is_named_not_raised(caplog, node_config):
    """A non-string YAML config key must not raise TypeError from this warning.

    ``sorted(k for k in node_config ...)`` raises ``TypeError: '<' not
    supported between instances of 'int' and 'str'`` on a mixed key set, and
    even a config with only int keys survives ``sorted`` only to blow up on
    ``", ".join(ignored)`` with ``TypeError: sequence item 0: expected str
    instance, int found``. A node whose YAML ``config:`` block carries a bare
    integer key -- ``compiler.py``'s ``config=node.get("config") or {}`` does
    no key coercion -- must still warn, not abort the node.
    """
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(ext, _shell(node_config))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    for key in node_config:
        assert str(key) in warnings[0].getMessage()


def test_a_recognised_node_config_warns_about_nothing(caplog):
    """The control. Without it the test above passes on an unconditional warn."""
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    ext.get_node_secret_info.return_value = {"aliases": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(
            ext,
            _shell(
                {
                    "input_parameters": {"values": {"temperature": 0.5}},
                    "secret_bindings": {"bindings": {"tok": "secret-1"}},
                }
            ),
        )
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


def test_an_empty_node_config_warns_about_nothing(caplog):
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(ext, _shell({}))
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


@pytest.mark.parametrize(
    "spelling",
    ["mainflow_target_node_ref", "mainflow_target_node_id", "mainflow_target_node_index"],
)
def test_a_mainflow_hop_config_key_warns_about_nothing(caplog, spelling):
    """The measured 1.2.1 defect: every config-only mainflow hop warned.

    ``perceivo._bundle._port_consistency`` deliberately writes
    ``config.mainflow_target_node_ref`` for a node whose mainflow continuation
    cannot be expressed as a real edge, and
    ``perceivo.orchestration._builder._bundle_project`` accepts the two raw
    spellings (``_id``, ``_index``) a platform export may carry before
    normalization (round-2 hop test,
    ``tests/unit/test_bundle_mainflow_port_export.py``). None of the three is
    dropped on the floor -- the graph compiler reads whichever is present to
    synthesize the MAINFLOW edge before the runner ever sees the shell -- so
    naming it "unrecognised" here was simply false.
    """
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    ext.get_node_secret_info.return_value = {"aliases": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(ext, _shell({spelling: "node_5"}))
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


def test_an_unknown_key_beside_a_mainflow_hop_key_still_warns(caplog):
    """The control: a real unrecognised key must still be named.

    Without this, the mainflow exemption above could pass by having quietly
    swallowed every warning rather than by correctly recognising the hop key.
    """
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(
            ext, _shell({"mainflow_target_node_ref": "node_5", "temperature": 0.5})
        )
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    message = warnings[0].getMessage()
    # The ignored-keys clause names only the real unknown key -- the mainflow
    # hop key appears elsewhere in the message (explaining what it is), but
    # must not be counted among the keys being ignored.
    assert "Ignoring 1 unrecognised node config key(s): temperature." in message


@pytest.mark.parametrize(
    "key",
    [
        "fsm",
        "port_sides",
        "port_order",
        "survey_spec",
        "required_secrets",
        "forward_input_to_states",
    ],
)
def test_a_platform_node_config_key_warns_about_nothing(caplog, key):
    """Keys the web builder writes or the hosted service reads are not ignored."""
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    ext.get_node_secret_info.return_value = {"aliases": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(ext, _shell({key: {"max_steps": 1}}))
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []


def test_an_unknown_key_beside_platform_keys_still_warns(caplog):
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(
            ext,
            _shell(
                {
                    "fsm": {"max_steps": 1},
                    "port_sides": {},
                    "port_order": {},
                    "temperature": 0.5,
                }
            ),
        )
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "Ignoring 1 unrecognised node config key(s): temperature." in warnings[0].getMessage()


def test_forward_input_to_states_is_exposed_as_a_parameter():
    """The hosted service sets the parameter to "true" when the node flag is set."""
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {"keep": 1}, "declarations": {"keep": {}}}
    _configure_ext_from_shell(
        ext,
        _shell(
            {
                "forward_input_to_states": True,
                "input_parameters": {"values": {"forward_input_to_states": "false", "top_p": 1}},
            }
        ),
    )
    ext.set_node_env.assert_called_once()
    assert ext.set_node_env.call_args.kwargs["values"] == {
        "keep": 1,
        "top_p": 1,
        "forward_input_to_states": "true",
    }


@pytest.mark.parametrize("flag", [False, None, ""])
def test_an_unset_forward_input_to_states_adds_no_parameter(flag):
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    _configure_ext_from_shell(ext, _shell({"forward_input_to_states": flag}))
    ext.set_node_env.assert_not_called()


def test_the_recognised_keys_are_still_read_after_warning(caplog):
    """A mixed config must warn AND still apply the half it understands."""
    ext = MagicMock()
    ext.get_node_env.return_value = {"values": {}, "declarations": {}}
    with caplog.at_level("WARNING", logger="perceivo.runner.config"):
        _configure_ext_from_shell(
            ext, _shell({"temperature": 0.5, "input_parameters": {"values": {"top_p": 1}}})
        )
    assert "temperature" in caplog.records[0].getMessage()
    ext.set_node_env.assert_called_once()
    assert ext.set_node_env.call_args.kwargs["values"] == {"top_p": 1}


def test_recognised_config_keys_matches_what_is_read():
    """The drift guard: a third reader cannot appear without being listed.

    ``RECOGNISED_CONFIG_KEYS`` drives the warning, so if someone adds a
    ``node_config.get("retry_policy")`` reader and forgets the constant, the
    runner starts warning about a key it does in fact read. Scanning the AST
    for every ``node_config.get(<literal>)`` in the module is a second,
    independent instrument -- it reads the code rather than the constant, so
    the two cannot agree by sharing a mistake.
    """
    import ast
    import inspect

    from perceivo.runner import _configuration

    tree = ast.parse(inspect.getsource(_configuration))
    read = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "node_config"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert read, "AST scan found no node_config.get(...) calls -- the guard is inert"
    assert read == set(_configuration.RECOGNISED_CONFIG_KEYS)
