"""Slug and nested-directory patterns stay ASCII-only.

The non-word class on a ``str`` pattern is Unicode-aware; the three patterns compile
it with ``re.ASCII`` so a non-ASCII letter or digit is still replaced, exactly as the
``[^A-Za-z0-9_]`` class they replaced did.
"""

from __future__ import annotations

import re

import pytest

from perceivo._bundle import _node_refs, capability
from perceivo.orchestration._builder import _graph_edit

_REFERENCE = re.compile(r"[^A-Za-z0-9_]+")
_PATTERNS = [_node_refs._SLUG, capability._SLUG, _graph_edit._DIR_UNSAFE]
_SAMPLES = ["café", "naïve-name", "Ünïcode_ok", "日本語", "x²y", "٣digits", "a b\tc", "ok_Name9"]


@pytest.mark.parametrize("pattern", _PATTERNS)
def test_non_ascii_word_characters_are_non_word(pattern: re.Pattern[str]) -> None:
    assert pattern.sub("-", "café") == "caf-"


@pytest.mark.parametrize("pattern", _PATTERNS)
@pytest.mark.parametrize("sample", _SAMPLES)
def test_pattern_matches_the_ascii_class_it_replaced(pattern: re.Pattern[str], sample: str) -> None:
    assert pattern.sub("-", sample) == _REFERENCE.sub("-", sample)


@pytest.mark.parametrize("pattern", _PATTERNS)
def test_every_code_point_below_0x3000_agrees(pattern: re.Pattern[str]) -> None:
    text = "".join(map(chr, range(1, 0x3000)))
    assert pattern.sub("-", text) == _REFERENCE.sub("-", text)
