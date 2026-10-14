"""A number or integer holding a ${field} reference is returned unchanged; a boolean raises."""

from __future__ import annotations

import pytest

from perceivo.params import _variables as env
from perceivo.types.errors import ConfigurationError

TYPES = ["number", "integer"]
BOOLEAN_ERROR = r"^Input parameter 'p' is declared as 'boolean'"


def _declare(stub_ext, declarations, values=None):
    data = {"values": values or {}, "declarations": declarations}
    stub_ext.get_node_env = lambda: data


@pytest.mark.parametrize("declared", TYPES)
@pytest.mark.parametrize("value", ["${x}", "${chunk.size}", "1${x}", "${a} ${b}"])
def test_a_reference_value_is_returned_unchanged(stub_ext, declared, value):
    _declare(stub_ext, {"p": {"type": declared, "default": "1"}}, values={"p": value})
    assert env.get("p") == value
    assert env.all() == {"p": value}


@pytest.mark.parametrize("declared", TYPES)
def test_a_reference_default_is_returned_unchanged(stub_ext, declared):
    _declare(stub_ext, {"p": {"type": declared, "default": "${x}"}})
    assert env.get("p") == "${x}"
    assert env.all() == {"p": "${x}"}


@pytest.mark.parametrize("value", ["${flag}", "${a} ${b}"])
def test_a_boolean_reference_value_raises(stub_ext, value):
    _declare(stub_ext, {"p": {"type": "boolean"}}, values={"p": value})
    with pytest.raises(ConfigurationError, match=BOOLEAN_ERROR):
        env.get("p")
    with pytest.raises(ConfigurationError, match=BOOLEAN_ERROR):
        env.all()


def test_a_boolean_reference_default_raises(stub_ext):
    _declare(stub_ext, {"p": {"type": "boolean", "default": "${flag}"}})
    with pytest.raises(ConfigurationError, match=BOOLEAN_ERROR):
        env.get("p")
    with pytest.raises(ConfigurationError, match=BOOLEAN_ERROR):
        env.all()


@pytest.mark.parametrize("declared", [*TYPES, "boolean"])
@pytest.mark.parametrize("value", ["abc", "${", "${}", "$x", "{x}"])
def test_a_value_without_a_complete_reference_still_raises(stub_ext, declared, value):
    _declare(stub_ext, {"p": {"type": declared}}, values={"p": value})
    with pytest.raises(
        ConfigurationError, match=f"^Input parameter 'p' is declared as '{declared}'"
    ):
        env.get("p")
    with pytest.raises(ConfigurationError):
        env.all()


@pytest.mark.parametrize("declared", [*TYPES, "boolean"])
def test_an_unreadable_default_without_a_reference_still_raises(stub_ext, declared):
    _declare(stub_ext, {"p": {"type": declared, "default": "${"}})
    with pytest.raises(
        ConfigurationError, match=f"^Input parameter 'p' is declared as '{declared}'"
    ):
        env.get("p")
