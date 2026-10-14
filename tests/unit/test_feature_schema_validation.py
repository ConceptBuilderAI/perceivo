"""``FeatureSchema`` refuses a schema whose names disagree.

A typo in a property key, an entity key or the timestamp field that is
accepted only surfaces as a feature view built on the wrong column. The
three static seed FEATURE schemas the platform ships are fixtures here and must
keep constructing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from perceivo.types import FeatureSchema, PropertyDefinition, PropertyType

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "seed_feature_schemas.json"
SEEDS = json.loads(FIXTURE.read_text(encoding="utf-8"))["schemas"]
USER_ID = "user_id"
TIMESTAMP = "event_timestamp"


def _properties(*names: str) -> dict[str, PropertyDefinition]:
    return {
        name: PropertyDefinition.model_validate({"name": name, "dtype": PropertyType.STRING})
        for name in names
    }


def test_valid_schema_constructs() -> None:
    schema = FeatureSchema.model_validate(
        {
            "properties": _properties(USER_ID, TIMESTAMP),
            "entity_key": [USER_ID],
            "timestamp_field": TIMESTAMP,
        }
    )
    assert schema.property_names == [USER_ID, TIMESTAMP]


@pytest.mark.parametrize("name", sorted(SEEDS))
def test_seed_schema_constructs(name: str) -> None:
    schema = FeatureSchema.model_validate(SEEDS[name])
    assert schema.timestamp_field in schema.properties


def test_seed_fixture_holds_the_three_static_schemas() -> None:
    assert sorted(SEEDS) == ["image_classifier", "recommender", "stock_predictor"]


def test_key_differing_from_name_is_refused() -> None:
    properties = _properties(USER_ID, TIMESTAMP)
    properties["userid"] = properties.pop(USER_ID)
    with pytest.raises(ValidationError, match="key 'userid' holds 'user_id'"):
        FeatureSchema.model_validate(
            {"properties": properties, "entity_key": ["userid"], "timestamp_field": TIMESTAMP}
        )


def test_unknown_entity_key_names_declared_properties() -> None:
    properties = _properties(USER_ID, TIMESTAMP)
    with pytest.raises(ValidationError, match=f"usr_id; declared: {USER_ID}, {TIMESTAMP}"):
        FeatureSchema.model_validate(
            {"properties": properties, "entity_key": ["usr_id"], "timestamp_field": TIMESTAMP}
        )


def test_unknown_timestamp_field_names_declared_properties() -> None:
    properties = _properties(USER_ID, TIMESTAMP)
    with pytest.raises(
        ValidationError, match=f"'ts' is not a declared property; declared: {USER_ID}"
    ):
        FeatureSchema.model_validate(
            {"properties": properties, "entity_key": [USER_ID], "timestamp_field": "ts"}
        )


def test_absent_timestamp_is_refused() -> None:
    properties = _properties(USER_ID)
    with pytest.raises(ValidationError, match="timestamp_field"):
        FeatureSchema.model_validate(
            {"properties": properties, "entity_key": [USER_ID], "timestamp_field": TIMESTAMP}
        )
