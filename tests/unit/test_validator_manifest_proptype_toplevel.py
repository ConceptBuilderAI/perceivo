"""MAN-PROPTYPE at the manifest's component-level positions.

The platform's manifest parser also refuses a non-canonical type in the top-level
``request_data_schema`` and in ``hyperparameters.<name>.type``. Port-level
coverage lives in ``test_validator_type_names.py``.
"""

from __future__ import annotations

import textwrap

import pytest

from perceivo.validator._manifest import parse_manifest
from perceivo.validator._type_names import PERMITTED_PROPERTY_TYPE_ORDER

#: Non-canonical spellings the platform refuses at its write boundary.
LEGACY_SPELLINGS = [
    "number",
    "double",
    "int",
    "str",
    "bool",
    "dict",
    "list",
    "text",
    "Integer",
    "STRING",
]


# ── Component-level request_data_schema and hyperparameters ─────────────


def _man_proptype(manifest: str, ctype: str) -> list[str]:
    _parsed, checks = parse_manifest(textwrap.dedent(manifest), ctype)
    return [c.message.split(". Permitted")[0] for c in checks if c.id.startswith("MAN-PROPTYPE")]


@pytest.mark.parametrize("spelling", LEGACY_SPELLINGS)
def test_manifest_top_level_request_data_schema_legacy_spelling_is_an_error(spelling):
    manifest = f"""        extension_type: START
        request_data_schema:
          n: {spelling}
          m: {{type: {spelling}}}
          l: "{spelling} (optional) - a value"
        """
    assert _man_proptype(manifest, "START") == [
        f"Invalid property type {spelling!r} at request_data_schema.n",
        f"Invalid property type {spelling!r} at request_data_schema.m",
        f"Invalid property type {spelling!r} at request_data_schema.l",
    ]


@pytest.mark.parametrize("spelling", PERMITTED_PROPERTY_TYPE_ORDER)
def test_manifest_top_level_request_data_schema_canonical_spelling_passes(spelling):
    manifest = f"""        extension_type: START
        request_data_schema:
          n: {spelling}
          m: {{type: {spelling}}}
          l: "{spelling} (optional) - a value"
          d: {{description: no type means string}}
        """
    assert _man_proptype(manifest, "START") == []


def test_manifest_top_level_request_data_schema_list_form():
    manifest = """        extension_type: START
        request_data_schema:
          - {name: a, type: number}
          - {name: b, type: integer}
          - {name: c}
        """
    assert _man_proptype(manifest, "START") == [
        "Invalid property type 'number' at request_data_schema.a",
    ]


#: Outside ``HYPERPARAMETER_TYPE_MAP`` -- rejected by ``map_source_type``.
_BAD_HYPERPARAMETER_TYPES = ["number", "double", "object", "array", "dict", "list", "text"]
#: Inside it, compared after ``strip().lower()`` as ``map_source_type`` does.
_GOOD_HYPERPARAMETER_TYPES = [
    "int",
    "integer",
    "float",
    "str",
    "string",
    "bool",
    "boolean",
    "Int",
    " FLOAT ",
]


@pytest.mark.parametrize("spelling", _BAD_HYPERPARAMETER_TYPES)
def test_manifest_hyperparameter_type_outside_its_vocabulary_is_an_error(spelling):
    manifest = f"""        extension_type: TRAINING
        hyperparameters:
          lr: {{type: {spelling}, default: 0.01}}
        """
    assert _man_proptype(manifest, "TRAINING") == [
        f"Invalid property type {spelling!r} at hyperparameters.lr.type",
    ]


@pytest.mark.parametrize("spelling", _GOOD_HYPERPARAMETER_TYPES)
def test_manifest_hyperparameter_type_in_its_vocabulary_passes(spelling):
    manifest = f"""        extension_type: TRAINING
        hyperparameters:
          lr: {{type: "{spelling}", default: 1}}
        """
    assert _man_proptype(manifest, "TRAINING") == []


def test_manifest_hyperparameter_untyped_forms_pass():
    # Absent / null ``type`` takes the parser default; a bare value is inferred.
    manifest = """        extension_type: TRAINING
        hyperparameters:
          a: {default: 0.5}
          b: {type: null, default: 1}
          c: 0.01
          d: number
        """
    assert _man_proptype(manifest, "TRAINING") == []


# ── The suggestion names the spelling it was given ──────────────────────


def _man_proptype_suggestions(manifest: str, ctype: str) -> list[str | None]:
    _parsed, checks = parse_manifest(textwrap.dedent(manifest), ctype)
    return [c.suggestion for c in checks if c.id.startswith("MAN-PROPTYPE")]


def test_manifest_proptype_suggestion_mentions_number_only_for_number():
    manifest = """        extension_type: START
        request_data_schema:
          a: number
          b: int
        """
    assert _man_proptype_suggestions(manifest, "START") == [
        "Spell the type as one of: string, integer, float, boolean, object, array. "
        "'number' is not a property type: declare 'integer' for a whole count "
        "or 'float' for a real.",
        "Spell the type as one of: string, integer, float, boolean, object, array.",
    ]


def test_manifest_proptype_suggestion_for_a_hyperparameter_lists_its_vocabulary():
    # object and array are property types but not hyperparameter types.
    manifest = """        extension_type: TRAINING
        hyperparameters:
          lr: {type: object, default: 0.01}
        """
    assert _man_proptype_suggestions(manifest, "TRAINING") == [
        "Spell the hyperparameter type as one of: string, integer, float, boolean.",
    ]
