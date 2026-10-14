"""T2: perceivo.params input-parameter access (implementation module)."""

from __future__ import annotations

import pytest

from perceivo.params import _variables as env
from perceivo.types.errors import ConfigurationError


class _EnvStub:
    def __init__(self, values=None, declarations=None):
        self._env = {
            "values": values or {},
            "declarations": declarations or {},
        }

    def get_node_env(self):
        return self._env


def test_env_get_outside_context_raises(cleared_context):
    with pytest.raises(RuntimeError):
        env.get("anything")


def test_env_get_coerces_number_to_float(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"temp": "0.7"},
        declarations={"temp": {"type": "number"}},
    ).get_node_env
    assert env.get("temp") == 0.7


def test_env_get_coerces_integer(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"k": "42"},
        declarations={"k": {"type": "integer"}},
    ).get_node_env
    assert env.get("k") == 42


def test_env_get_coerces_boolean_strings(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"on": "true", "off": "no"},
        declarations={
            "on": {"type": "boolean"},
            "off": {"type": "boolean"},
        },
    ).get_node_env
    assert env.get("on") is True
    assert env.get("off") is False


def test_env_get_coerces_multi_select(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"tags": "a, b, c"},
        declarations={"tags": {"type": "multi_select"}},
    ).get_node_env
    assert env.get("tags") == ["a", "b", "c"]


def test_env_get_uses_manifest_default_when_no_value(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={},
        declarations={"x": {"type": "text", "default": "hello"}},
    ).get_node_env
    assert env.get("x") == "hello"


def test_env_get_uses_caller_default(stub_ext):
    stub_ext.get_node_env = _EnvStub().get_node_env
    assert env.get("missing", "fallback") == "fallback"


def test_env_get_missing_raises_key_error(stub_ext):
    stub_ext.get_node_env = _EnvStub().get_node_env
    with pytest.raises(KeyError, match="not set and has no default"):
        env.get("missing")


def test_env_get_coercion_failure_raises(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"k": "not-a-number"},
        declarations={"k": {"type": "integer"}},
    ).get_node_env
    with pytest.raises(
        ConfigurationError,
        match=r"^Input parameter 'k' is declared as 'integer', and its str value cannot be read",
    ):
        env.get("k")


def test_env_keys_merges_values_and_declarations(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"a": 1, "b": 2},
        declarations={"b": {"type": "integer"}, "c": {"type": "text"}},
    ).get_node_env
    assert env.keys() == ["a", "b", "c"]


def test_env_get_declaration_returns_dict_or_none(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        declarations={"x": {"type": "text", "label": "X"}},
    ).get_node_env
    assert env.declaration("x").label == "X"
    assert env.declaration("nope") is None


def test_env_get_all_returns_all_coerced(stub_ext):
    stub_ext.get_node_env = _EnvStub(
        values={"a": "1", "b": "true"},
        declarations={
            "a": {"type": "integer"},
            "b": {"type": "boolean"},
            "c": {"type": "text", "default": "hi"},
        },
    ).get_node_env
    assert env.all() == {"a": 1, "b": True, "c": "hi"}
