"""Rule 8 of ``scripts/vendor_template_library.py``: no repository names in vendored text.

The public SDK names no internal repository. Rule 8 turns a platform repository
name, recognised by its shape, into a description, and a vendored file in which
one survives refuses the vendor. The names below are synthetic: the rule is a
shape, so it needs no list of real names.
"""

from __future__ import annotations

import pytest

from scripts import vendor_template_library as vendor


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see perceivo-widget-service, app/x.py", "see the platform widget service, app/x.py"),
        ("# (perceivo-deep-thought-manager)", "# (the platform deep thought manager)"),
        ("perceivo-ledger-microservice routes", "the platform ledger service routes"),
        (
            "perceivo-edge-gateway and perceivo-queue-broker",
            "the platform edge gateway and the platform queue broker",
        ),
    ],
)
def test_rule_8_describes_a_repository_by_its_shape(text: str, expected: str) -> None:
    assert vendor.describe_repositories(text) == expected


@pytest.mark.parametrize(
    "text", ["import perceivo", "perceivo-sdk", "perceivo.primitives.FsmOutput", "my-service"]
)
def test_rule_8_leaves_everything_else_alone(text: str) -> None:
    assert vendor.describe_repositories(text) == text


def test_rule_8_runs_as_part_of_the_rewrite() -> None:
    assert 8 in vendor.RULES
    assert (
        vendor.rewrite_text("# perceivo-widget-service", "any") == "# the platform widget service"
    )
    assert (
        vendor.rewrite_text("# perceivo-widget-service", "any", rules=(7,))
        == "# perceivo-widget-service"
    )


def test_a_surviving_repository_name_refuses_the_vendor() -> None:
    with pytest.raises(SystemExit, match="a platform repository name survives"):
        vendor.refuse_repository_names("x.json", b'{"script": "# perceivo-widget-service"}')
    vendor.refuse_repository_names("x.json", b'{"script": "# the platform widget service"}')
