"""The ``PerceivoError`` contract, and an honest measurement of where it fails.

``perceivo/types/errors.py`` makes one promise: *catch ``PerceivoError`` and you
catch everything the SDK raises.* Two kinds of test live here.

The first kind proves the promise for the classes the SDK **declares** -- they
are re-rooted, and they keep their original bases so no existing ``except``
clause stops working.

The second kind measures where the promise is still false. Bare
``ValueError`` / ``RuntimeError`` / ``KeyError`` are raised inline across the
SDK, and no amount of re-rooting reaches them. Rather than leave that as a
footnote nobody re-reads, the number is pinned: the count may fall, never rise.
A new bare raise fails this test and the author has to either use a typed error
or argue for the exception in review. That is the mechanism by which the gap
closes instead of being quietly forgotten.
"""

from __future__ import annotations

import ast
import collections
import pathlib

import pytest

import perceivo
from perceivo.types import errors as errors_mod

SDK_ROOT = pathlib.Path(perceivo.__file__).parent

#: Builtins whose bare use defeats ``except PerceivoError``. ``TypeError`` and
#: ``NotImplementedError`` are deliberately **excluded**: an abstract method
#: raising ``NotImplementedError`` and an argument-type guard raising
#: ``TypeError`` are Python contracts, not SDK errors, and a caller reaching for
#: ``PerceivoError`` is not trying to catch a programming mistake in their own
#: call.
UNROOTED_BUILTINS = frozenset({"ValueError", "RuntimeError", "KeyError"})

#: This number is a ratchet, not a target: see the module docstring. If a
#: refactor legitimately lowers it, lower it here in the same commit -- that is
#: the point.
#:
#: runner/loader.py rejects ENTRY_CLASS bound to a class object or an instance
#: with a bare RuntimeError, because the orchestration sandbox resolves it with
#: getattr(module, name) and would raise TypeError there. The module raises
#: bare RuntimeError for five other load failures (a missing file is
#: FileNotFoundError) and test_runner_loader.py matches on it, so rooting this
#: one alone would split the convention rather than close the gap.
#:
#: ``perceivo/types/training.py``'s ``_require_finite_metrics`` raises
#: ``ValueError`` twice -- once for a non-numeric metric, once for NaN/inf.
#:
#: Neither escapes. Both sit inside a ``@field_validator``, and pydantic catches
#: ``ValueError`` from a validator by contract and re-raises it as
#: ``pydantic_core.ValidationError``. Measured, not assumed:
#: ``EvaluationResult(metrics={"x": float("nan")})`` and the same on
#: ``IterationResult`` both surface ``pydantic_core._pydantic_core.ValidationError``.
#: So the sentence in the assertion message -- "these escape
#: ``except PerceivoError``" -- is not true of these two, and neither is its
#: converse: rooting them at a class that is not a ``ValueError`` would take them
#: OUT of pydantic's contract and turn a clean field-validation failure into an
#: uncaught exception. The framework requires the builtin here.
#:
#: They are counted rather than exempted on purpose. An exemption for "raises
#: under a decorator" is a rule the next reader would have to trust; a recorded
#: number with the reason attached is one they can re-measure. The ratchet
#: does its job: a 112th raise fails the build.
#:
#: ``perceivo/params/_variables.py``'s ``_parse_bool`` raises ``ValueError`` for
#: a value that is no boolean. It never escapes: ``_coerce`` catches
#: ``ValueError`` from every coercion, the ``float`` and ``int`` builtins
#: included, and re-raises ``ConfigurationError``. A rooted class here would be
#: caught by the same clause and never seen.
#:
#: ``perceivo/orchestration/_builder/_prebuilt.py`` refuses START and END with a
#: ``ValueError``. Only the builder calls it, never with a terminal type, so it
#: guards a programming error rather than an author's input.
#:
#: The import file-name and sidecar refusals (``_bundle/_file_names.py``,
#: ``_bundle/_component_sidecar.py``) are rooted: ``UnsafeFileNameError``,
#: ``PathTraversalError`` and ``BundleConfigError``, all still ``ValueError``.
BARE_RAISE_BUDGET = 111


def _bare_raises() -> collections.Counter[str]:
    """Count ``raise <builtin>(...)`` per file across the shipped package.

    Uses the AST rather than a grep so that a builtin name inside a string, a
    comment, or a docstring is not counted, and so ``raise SomeError`` and
    ``raise SomeError(...)`` are both seen.
    """
    per_file: collections.Counter[str] = collections.Counter()
    for path in sorted(SDK_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc
            called = exc.func if isinstance(exc, ast.Call) else exc
            if isinstance(called, ast.Name) and called.id in UNROOTED_BUILTINS:
                per_file[path.relative_to(SDK_ROOT).as_posix()] += 1
    return per_file


class TestTheDeclaredHierarchy:
    """Everything ``errors.py`` exports really is rooted where it claims."""

    def test_every_exported_error_is_a_perceivo_error(self) -> None:
        for name in errors_mod.__all__:
            cls = getattr(errors_mod, name)
            assert issubclass(cls, errors_mod.PerceivoError), name

    def test_the_hierarchy_is_reachable_from_the_types_plane(self) -> None:
        # A hierarchy authors cannot import is a hierarchy authors will not use.
        # The root holds planes and decorators only, so the errors live on
        # ``perceivo.types``.
        assert perceivo.types.PerceivoError is errors_mod.PerceivoError
        for name in errors_mod.__all__:
            assert getattr(perceivo.types, name) is getattr(errors_mod, name), name
            assert name in perceivo.types.__all__, name
            assert not hasattr(perceivo, name), name

    def test_access_denied_error_is_not_the_builtin(self) -> None:
        # Named so it cannot shadow the builtin. If it ever became the builtin,
        # `except AccessDeniedError` would start catching filesystem failures.
        assert errors_mod.AccessDeniedError is not PermissionError
        assert not issubclass(errors_mod.AccessDeniedError, OSError)
        assert not hasattr(errors_mod, "PermissionError_")

    def test_service_error_carries_retry_advice(self) -> None:
        err = errors_mod.ServiceError("boom", retryable=True, status_code=503)
        assert err.retryable is True
        assert err.status_code == 503

    def test_service_error_does_not_assume_a_failure_is_safe_to_repeat(self) -> None:
        # The default matters more than the flag: an unknown failure retried
        # blindly is how a single 500 becomes a thundering herd.
        assert errors_mod.ServiceError("boom").retryable is False
        assert errors_mod.ServiceError("boom").status_code is None


class TestErrorsWithAStdlibBaseArePerceivoErrors:
    """The re-rooting widened what can be caught and took nothing away.

    Each case asserts *both* directions, because only asserting the new base
    would let a refactor drop the original one and break every caller that
    catches it.
    """

    @staticmethod
    def _cases() -> list[tuple[type[BaseException], type[BaseException]]]:
        """The re-rooted errors that still exist.

        Both directions have a witness, which is what makes these two
        sufficient: one class per original base (``RuntimeError`` and
        ``ValueError``), so a refactor that re-rooted onto ``PerceivoError`` by
        *replacing* the original base rather than adding to it fails here.
        """
        from perceivo._internal.auth import MissingCredentialsError
        from perceivo.local.adapters._safe_path import PathTraversalError

        return [
            (MissingCredentialsError, RuntimeError),
            (PathTraversalError, ValueError),
        ]

    def test_each_is_a_perceivo_error(self) -> None:
        for cls, _stdlib_base in self._cases():
            assert issubclass(cls, errors_mod.PerceivoError), cls.__name__

    def test_each_keeps_its_stdlib_base(self) -> None:
        for cls, stdlib_base in self._cases():
            assert issubclass(cls, stdlib_base), f"{cls.__name__} lost {stdlib_base.__name__}"

    def test_a_missing_credential_is_a_configuration_error(self) -> None:
        # Not a ServiceError: nothing is failing. The component declared a
        # secret that is unbound, which fails identically on every attempt.
        from perceivo._internal.auth import MissingCredentialsError

        assert issubclass(MissingCredentialsError, errors_mod.ConfigurationError)

    def test_catching_the_root_catches_a_real_raise(self) -> None:
        from perceivo.local.adapters._safe_path import PathTraversalError

        with pytest.raises(errors_mod.PerceivoError):
            raise PathTraversalError("../../etc/passwd")


class TestTheGapIsMeasuredNotHandWaved:
    """The part of the promise that is still false, kept visible."""

    def test_bare_builtin_raises_do_not_grow(self) -> None:
        per_file = _bare_raises()
        total = sum(per_file.values())
        assert total <= BARE_RAISE_BUDGET, (
            f"bare {sorted(UNROOTED_BUILTINS)} raises rose to {total} "
            f"(budget {BARE_RAISE_BUDGET}). These escape `except PerceivoError`. "
            f"Use a perceivo.types.errors class, or lower the budget knowingly. "
            f"Worst files: {per_file.most_common(5)}"
        )

    def test_the_budget_is_not_stale(self) -> None:
        # A ratchet that is never tightened is a ratchet nobody is turning. If
        # the real count drops, this fails and the budget comes down with it,
        # so the recorded number stays an honest description of the tree.
        total = sum(_bare_raises().values())
        assert total == BARE_RAISE_BUDGET, (
            f"bare raises fell to {total}; lower BARE_RAISE_BUDGET to match"
        )

    def test_the_counter_actually_counts(self) -> None:
        # A measurement that would report zero on a broken walk is not a
        # measurement. Assert the AST pass finds the known-worst offender.
        per_file = _bare_raises()
        assert per_file, "the AST walk found no bare raises at all -- it is broken"
        assert per_file["local/adapters/infra.py"] > 0
