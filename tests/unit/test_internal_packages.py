"""Declared-dependency resolution for ``additional_packages``.

Resolution is by distribution metadata, not by importing. Both halves of that
matter and both are asserted here: a declaration whose import name differs from
its distribution name must still resolve, and a declaration that is genuinely
absent must be reported rather than swallowed.
"""

from __future__ import annotations

import pytest

from perceivo._internal.packages import (
    is_installed,
    missing_packages,
    missing_packages_warning,
    requirement_name,
)

_ABSENT = "unavailable-not-a-real-distribution"


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("httpx", "httpx"),
        ("  httpx  ", "httpx"),
        ("httpx>=0.27", "httpx"),
        ("httpx[http2]>=0.27,<1", "httpx"),
        ("httpx[http2]>=0.27 ; python_version>='3.11'", "httpx"),
        ("beautifulsoup4==4.12.3", "beautifulsoup4"),
        ("ruamel.yaml", "ruamel.yaml"),
        ("typing_extensions", "typing_extensions"),
        # Unparseable: no leading alphanumeric to anchor a distribution name.
        ("", ""),
        ("   ", ""),
        ("-broken", ""),
        ("@ git+https://example.invalid/x.git", ""),
    ],
)
def test_requirement_name_strips_everything_but_the_distribution(declaration, expected):
    assert requirement_name(declaration) == expected


def test_is_installed_resolves_by_distribution_not_import_name():
    """PyYAML is installed and imports as ``yaml`` -- the names differ."""
    import yaml  # noqa: F401  (proves the distribution really is present)

    assert is_installed("pyyaml") is True
    assert is_installed("PyYAML>=6") is True
    # The control: the same call on a distribution nothing installs says False,
    # so True above is a resolution and not a function that always agrees.
    assert is_installed(_ABSENT) is False


def test_is_installed_reports_an_unparseable_declaration_as_absent():
    assert is_installed("-broken") is False


def test_missing_packages_keeps_order_and_drops_duplicates():
    declared = ["pyyaml", _ABSENT, "  ", "pyyaml", f"{_ABSENT}", "unavailable-other"]

    assert missing_packages(declared) == [_ABSENT, "unavailable-other"]


def test_missing_packages_of_nothing_is_empty():
    assert missing_packages(None) == []
    assert missing_packages([]) == []


def test_missing_packages_warning_names_every_package_and_the_remedy():
    text = missing_packages_warning([_ABSENT, "other"], where="This orchestration")

    assert "This orchestration" in text
    assert _ABSENT in text
    assert "other" in text
    assert f"pip install {_ABSENT} other" in text
