"""A manifest default is coerced to the declared type; an unreadable one raises."""

from __future__ import annotations

import pytest

from perceivo.params import _variables as env
from perceivo.types.errors import ConfigurationError


def _declare(stub_ext, declarations, values=None):
    data = {"values": values or {}, "declarations": declarations}
    stub_ext.get_node_env = lambda: data


@pytest.mark.parametrize(
    ("declared", "default", "expected"),
    [
        ("number", "0.5", 0.5),
        ("number", 2, 2.0),
        ("integer", "3", 3),
        ("boolean", "true", True),
        ("multi_select", "a, b", ["a", "b"]),
        ("text", 7, "7"),
    ],
)
def test_a_manifest_default_is_coerced(stub_ext, declared, default, expected):
    _declare(stub_ext, {"p": {"type": declared, "default": default}})
    value = env.get("p")
    assert value == expected
    assert type(value) is type(expected)


def test_a_null_default_is_none(stub_ext):
    _declare(stub_ext, {"p": {"type": "integer", "default": None}})
    assert env.get("p") is None


def test_an_unreadable_default_raises_naming_the_parameter_and_type(stub_ext):
    _declare(stub_ext, {"p": {"type": "integer", "default": "many"}})
    with pytest.raises(
        ConfigurationError,
        match=r"^Input parameter 'p' is declared as 'integer', and its str value cannot be read",
    ):
        env.get("p")


def test_an_unreadable_value_raises_rather_than_returning_raw(stub_ext):
    _declare(stub_ext, {"p": {"type": "number"}}, values={"p": "fast"})
    with pytest.raises(ConfigurationError, match=r"^Input parameter 'p' is declared as 'number'"):
        env.get("p")


def test_all_coerces_defaults(stub_ext):
    _declare(stub_ext, {"n": {"type": "integer", "default": "4"}})
    assert env.all() == {"n": 4}


@pytest.mark.parametrize("value", ["maybe", " ", 2])
def test_an_unreadable_boolean_raises_rather_than_reading_false(stub_ext, value):
    _declare(stub_ext, {"p": {"type": "boolean"}}, values={"p": value})
    with pytest.raises(ConfigurationError, match=r"^Input parameter 'p' is declared as 'boolean'"):
        env.get("p")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("TRUE", True),
        ("yes", True),
        ("1", True),
        (1, True),
        ("False", False),
        ("no", False),
        ("0", False),
        (0, False),
        (False, False),
    ],
)
def test_a_readable_boolean_is_read(stub_ext, value, expected):
    _declare(stub_ext, {"p": {"type": "boolean"}}, values={"p": value})
    assert env.get("p") is expected


# The platform stores and forwards node values without checking them against the
# declared type, so an unset boolean can arrive as "".
@pytest.mark.parametrize("declared_default", [True, False, "true", "no"])
def test_an_empty_boolean_takes_the_declared_default(stub_ext, declared_default):
    _declare(stub_ext, {"p": {"type": "boolean", "default": declared_default}}, values={"p": ""})
    assert env.get("p") is env._parse_bool(declared_default)


def test_an_empty_boolean_with_no_declared_default_is_absent(stub_ext):
    _declare(stub_ext, {"p": {"type": "boolean"}}, values={"p": ""})
    with pytest.raises(KeyError, match="'p' is not set"):
        env.get("p")
    assert env.get("p", True) is True
    assert env.all() == {}


def test_an_empty_declared_boolean_default_is_no_default(stub_ext):
    _declare(stub_ext, {"p": {"type": "boolean", "default": ""}})
    with pytest.raises(KeyError, match="'p' is not set"):
        env.get("p")
    assert env.get("p", False) is False


def test_an_empty_text_value_is_still_a_value(stub_ext):
    _declare(stub_ext, {"p": {"type": "text", "default": "d"}}, values={"p": ""})
    assert env.get("p") == ""


# The node editor writes "" when a number or integer field is cleared.
@pytest.mark.parametrize(
    ("declared", "declared_default", "expected"), [("number", "2.5", 2.5), ("integer", 3, 3)]
)
def test_an_empty_number_takes_the_declared_default(stub_ext, declared, declared_default, expected):
    _declare(stub_ext, {"n": {"type": declared, "default": declared_default}}, values={"n": ""})
    assert env.get("n") == expected


@pytest.mark.parametrize("declared", ["number", "integer"])
def test_an_empty_number_with_no_declared_default_is_absent(stub_ext, declared):
    _declare(stub_ext, {"n": {"type": declared}, "t": {"type": "text"}}, values={"n": "", "t": "x"})
    with pytest.raises(KeyError, match="'n' is not set"):
        env.get("n")
    assert env.get("n", 5) == 5
    assert env.all() == {"t": "x"}


@pytest.mark.parametrize("declared", ["number", "integer"])
def test_an_empty_declared_number_default_is_no_default(stub_ext, declared):
    _declare(stub_ext, {"n": {"type": declared, "default": ""}})
    with pytest.raises(KeyError, match="'n' is not set"):
        env.get("n")
    assert env.get("n", 5) == 5


@pytest.mark.parametrize("declared", ["number", "integer"])
@pytest.mark.parametrize("value", ["abc", " ", [1]])
def test_an_unreadable_number_still_raises(stub_ext, declared, value):
    _declare(stub_ext, {"n": {"type": declared}}, values={"n": value})
    with pytest.raises(
        ConfigurationError, match=f"^Input parameter 'n' is declared as '{declared}'"
    ):
        env.get("n")
