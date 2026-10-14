"""Rule 9 of ``scripts/vendor_template_library.py``: no platform source cite in a script.

The public SDK cites no platform source file. Rule 9 drops, from
``error-handler`` only, the clause naming the platform engine's file, and keeps
the message the clause introduces. The text below is synthetic: the rule is a
shape, so it needs no real path.
"""

from __future__ import annotations

import json

import pytest

from scripts import vendor_template_library as vendor

CITED = '# raises\n# (the platform widget service, app/x/y.py:\n# "no edge"), so\n'
STRIPPED = '# raises\n# ("no edge"), so\n'


def test_rule_9_drops_the_clause_and_keeps_the_message() -> None:
    assert vendor.rewrite_text(CITED, "error-handler") == STRIPPED


def test_rule_9_touches_no_other_bundle() -> None:
    assert vendor.rewrite_text(CITED, "iterator") == CITED


@pytest.mark.parametrize("count", [0, 1, 2])
def test_rule_9_counts_the_clause_across_the_bundle(count: int) -> None:
    bundle = {"nodes": [{"script": CITED}] * count, "description": "plain"}
    assert vendor.rule9_hits(bundle) == count


def test_the_vendored_error_handler_carries_no_platform_source_path() -> None:
    source = json.loads((vendor.LIBRARY / "SOURCE.json").read_text(encoding="utf-8"))
    assert 9 in source["rules"]
    entry = source["entries"]["error-handler"]
    text = (vendor.LIBRARY / entry["file"]).read_text(encoding="utf-8")
    assert vendor.rule9_hits(json.loads(text)) == 0
    assert "app/" not in text
