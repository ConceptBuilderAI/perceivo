"""The validator's ML table describes the base classes this SDK really ships.

``COMPONENT_REQUIRED_METHODS`` and ``COMPONENT_BASES`` decide whether a
customer's component is judged valid. ``perceivo.intelligence`` carries
all four ML base classes, an extension's ``import perceivo`` binds this wheel,
and the measurement is an attribute read on a package the test already
imports.

**That is the difference between a gate and a skip.** A measurement that
looked for the classes anywhere else -- a runner checkout, say -- would find
nothing, skip every assertion below as "unverified", and let the table drift to
anything at all without one red test. A gate whose subject is missing does not
fail; it goes quiet.

Only ``@abstractmethod`` members are required, read from
``__abstractmethods__`` rather than inferred from decorator syntax -- the
class object knows, and inheritance is accounted for without walking bases by
hand. A concrete helper a component MAY override -- ``FeatureComponent``'s
``prepare_materialize`` and ``generate_inference_sample``,
``InferenceComponent``'s ``create_model`` and ``postprocess`` -- is
deliberately absent from the table: requiring one would fail a component for
declining an option.
"""

from __future__ import annotations

import importlib

import pytest

from perceivo.validator._component_tables import (
    ALL_COMPONENT_TYPES,
    COMPONENT_BASES,
    COMPONENT_REQUIRED_METHODS,
)

#: The four component types whose base class is an ML extension. Each is the
#: name a component author writes after ``from perceivo.intelligence import``.
ML_COMPONENT_TYPES: tuple[str, ...] = ("FEATURE", "TRAINING", "INFERENCE", "EVALUATION")

#: The import path the table's base classes must be reachable at. This is the
#: one a component author uses and the one the runners' loaders resolve, so a
#: class that exists in the tree but is not exported here is still unusable.
COMPONENTS_MODULE = "perceivo.intelligence"


def _base_class(ctype: str) -> type:
    """The class ``COMPONENT_BASES[ctype]`` names, resolved through the package."""
    module = importlib.import_module(COMPONENTS_MODULE)
    name = COMPONENT_BASES[ctype]
    cls: object = getattr(module, name, None)
    assert cls is not None, (
        f"COMPONENT_BASES[{ctype!r}] names {name!r}, which {COMPONENTS_MODULE} does "
        f"not export. Every {ctype} component would draw a base-class-mismatch "
        f"warning for inheriting from the right class."
    )
    assert isinstance(cls, type), (
        f"COMPONENT_BASES[{ctype!r}] names {name!r}, but that export is a "
        f"{type(cls).__name__}, not a class."
    )
    return cls


def test_ml_table_keys_are_real_component_types() -> None:
    """Every keyed type exists, so no entry is dead weight nothing can reach.

    This is the assertion that catches an entry filed under a type the platform
    does not have: the four EvaluationComponent methods sat under a ``MANAGER``
    key, which is absent from ``ALL_COMPONENT_TYPES``, so they were unreachable
    and every real ``EVALUATION`` component was validated with no required
    methods at all.
    """
    for label, table in (
        ("COMPONENT_REQUIRED_METHODS", COMPONENT_REQUIRED_METHODS),
        ("COMPONENT_BASES", COMPONENT_BASES),
    ):
        unknown = sorted(set(table) - ALL_COMPONENT_TYPES)
        assert not unknown, (
            f"{label} keys a component type that does not exist: {unknown}. "
            "Nothing can ever reach these entries, and the real type they were "
            "meant for is validated without them."
        )


@pytest.mark.parametrize("ctype", ML_COMPONENT_TYPES)
def test_ml_base_class_is_exported_by_the_sdk(ctype: str) -> None:
    """``COMPONENT_BASES`` names a class ``perceivo.intelligence`` actually exports.

    Not hypothetical: ``COMPONENT_BASES["FEATURE"]`` once named
    ``FeatureServiceExtension``, a class that exists nowhere, so every correctly
    written FEATURE component drew a base-class-mismatch warning.
    """
    assert ctype in COMPONENT_BASES, (
        f"{ctype} has no COMPONENT_BASES entry, but {COMPONENTS_MODULE} ships a "
        f"base class for it. The validator cannot check what it does not name."
    )
    cls = _base_class(ctype)
    assert isinstance(cls, type), f"{COMPONENT_BASES[ctype]} is not a class: {cls!r}"


@pytest.mark.parametrize("ctype", ML_COMPONENT_TYPES)
def test_ml_required_methods_are_the_sdk_abstract_methods(ctype: str) -> None:
    """``COMPONENT_REQUIRED_METHODS`` equals the base class's abstract set.

    Equality, not containment, in both directions: a name the table requires but
    the base does not declare fails a correct component, and an abstract method
    the table omits lets an incomplete component pass validation and blow up in
    the runner at ``TypeError: Can't instantiate abstract class``.
    """
    cls = _base_class(ctype)
    required = set(COMPONENT_REQUIRED_METHODS.get(ctype, []))
    abstract = set(getattr(cls, "__abstractmethods__", ()))
    assert required == abstract, (
        f"{ctype} ({COMPONENT_BASES[ctype]}): table requires {sorted(required)}, "
        f"class declares abstract {sorted(abstract)} "
        f"[table-only: {sorted(required - abstract)}, "
        f"unrequired-abstract: {sorted(abstract - required)}]"
    )


def test_every_ml_type_is_covered() -> None:
    """``ML_COMPONENT_TYPES`` is the full set, so no ML type escapes the checks.

    The two parametrized tests above only measure what this tuple lists. Add a
    fifth intelligence component to ``perceivo.intelligence`` and forget this line, and its
    table entry is unmeasured while the suite stays green -- the same silence
    a measurement of the wrong package produces, arriving by a different road.
    """
    module = importlib.import_module(COMPONENTS_MODULE)
    shipped = {
        name
        for name in dir(module)
        if name.endswith("Component") and isinstance(getattr(module, name), type)
    }
    covered = {COMPONENT_BASES[c] for c in ML_COMPONENT_TYPES}
    assert shipped == covered, (
        f"{COMPONENTS_MODULE} exports extension base classes this file does not "
        f"measure: {sorted(shipped - covered)}. Add the component type to "
        f"ML_COMPONENT_TYPES (and to COMPONENT_BASES) or explain the omission "
        f"here. [measured-but-not-shipped: {sorted(covered - shipped)}]"
    )
