"""What the ``managed_service`` plane *contains*, and ``database``.

The sibling module ``test_managed_service_plane.py`` covers how an AI call
resolves — unconfigured to the mock, configured to the real service, real
without a credential to an error. This one covers the three questions that are
about the plane's shape rather than about one call's fate:

1. **Which capabilities the enum admits.** The design requires all nine members
   and only some are backed, so the partition has to be read off the enum
   rather than off prose — a member that is silently neither backed nor
   declared unbacked is the failure that returns a CHAT mock in answer to a
   safety check.
2. **``managed_service.database``.** ``connect(id).query(sql)`` sends what
   ``database_query(id, sql)`` sends, so the preservation claim for that half
   is asserted here as payload identity against the private backend module the
   segment forwards to (:mod:`perceivo.managed_service._backend`).
3. **Plane hygiene.** An object segment MUST NOT be callable, and the eagerly
   imported planes MUST NOT drag the local plane into every runner's module
   graph. Both are structural, both
   fail silently, and neither is visible from a passing call.

The ``clean_config`` fixture the enum slice requests is shared with the sibling
module and lives in ``fixtures_managed_service.py``, registered through
``tests/unit/conftest.py``. No credential appears in this file and nothing here
opens a socket.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext
from perceivo.managed_service import _backend
from perceivo.types.errors import ConfigurationError, ServiceError

from .fixtures_managed_service import _AI

_DATABASE = perceivo.managed_service.database


# ---------------------------------------------------------------------------
# The enum: nine members, two backed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "service",
    [
        perceivo.types.AIService.SPEECH_TO_TEXT,
        perceivo.types.AIService.TEXT_TO_SPEECH,
        perceivo.types.AIService.VISION,
        perceivo.types.AIService.TRANSLATION,
        perceivo.types.AIService.DOCUMENT_INTEL,
    ],
)
async def test_an_unbacked_capability_says_so(service, clean_config, cleared_context):
    """The design requires all nine; these five are not built.

    Raising is the honest state and the message says the capability is not
    backed yet, so the reader learns it is scheduled server work rather than an
    SDK oversight. What must never
    happen is one of these quietly returning the CHAT mock: the seeded assets
    call this vocabulary, and a plausible-looking answer to a safety check is
    worse than an error.

    ``GROUNDEDNESS`` is backed and covered by ``test_groundedness_*`` below;
    ``CONTENT_SAFETY`` is covered by ``test_managed_service_prompt_shield.py``.
    Both are deliberately named here: the reason the rule exists is the seeded
    assets that reach for these capabilities, and two of them reach for a
    capability that answers.
    """
    with pytest.raises(NotImplementedError) as excinfo:
        await _AI.invoke(service, text="anything")
    assert "not backed" in str(excinfo.value)


async def test_the_backed_and_unbacked_members_partition_the_enum():
    """No member may be silently neither. Read off the enum, not off the prose."""
    backed = {
        perceivo.types.AIService.CHAT,
        perceivo.types.AIService.EMBEDDING,
        perceivo.types.AIService.GROUNDEDNESS,
        perceivo.types.AIService.CONTENT_SAFETY,
    }
    assert backed | _AI._UNBACKED == set(perceivo.types.AIService)
    assert not backed & _AI._UNBACKED
    # The third set, ``_PLATFORM_ONLY``, is gated in
    # ``test_managed_service_prompt_shield.py`` -- this file is at its
    # size cap, and adding another member made that set need a gate.


# ---------------------------------------------------------------------------
# GROUNDEDNESS: the member that left _UNBACKED
# ---------------------------------------------------------------------------


class _GroundednessExt:
    """The one extension-manager method this capability forwards to."""

    _DEFAULT = {"ungroundedPercentage": 0.25}

    def __init__(self, result=_DEFAULT):
        # Sentinel default, not ``None``: ``None`` is the value the failure test
        # needs to inject, so it cannot also mean "give me the default".
        self.result = result
        self.seen: dict = {}

    async def check_groundedness(self, **kwargs):
        self.seen = kwargs
        return self.result


class _RuntimeWithoutGroundedness:
    """A runtime image that predates the capability. Not a hypothetical: the
    orchestration and evaluation runtimes ship separately and update
    separately, so one of them is always the older one."""


async def test_groundedness_forwards_to_the_extension_manager(cleared_context):
    """The whole point of leaving ``_UNBACKED``: the call reaches the runtime.

    Asserted on the keyword names, not just on the return, because the extension
    manager and the data client between here and Azure both take these by
    keyword -- a positional forward would land ``query`` in ``grounding_sources``
    and score the answer against the question.
    """
    ext = _GroundednessExt()
    _ExecutionContext.set_ext(ext)
    result = await _AI.invoke(
        perceivo.types.AIService.GROUNDEDNESS,
        text="the answer",
        grounding_sources=["source one", "source two"],
        query="the question",
    )
    assert result == {"ungroundedPercentage": 0.25}
    assert ext.seen == {
        "text": "the answer",
        "grounding_sources": ["source one", "source two"],
        "query": "the question",
    }


async def test_groundedness_on_a_runtime_that_does_not_serve_it(cleared_context):
    """``NotImplementedError`` naming the runtime, never ``AttributeError``.

    This is the exact failure that produced 83 unscored runs: an extension
    manager without the method the SDK forwards to, surfacing as
    ``AttributeError: 'ExtensionManager' object has no attribute ...``, which
    reads as an SDK defect and sent the investigation to the wrong repository.
    """
    _ExecutionContext.set_ext(_RuntimeWithoutGroundedness())
    with pytest.raises(NotImplementedError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.GROUNDEDNESS, text="t", grounding_sources=["s"])
    message = str(excinfo.value)
    assert "_RuntimeWithoutGroundedness" in message, "the runtime must be named"
    assert "check_groundedness" in message, "the missing method must be named"


async def test_a_failed_groundedness_call_is_not_a_score_of_zero(cleared_context):
    """The evaluation runtime's data client returns ``None`` on an HTTP error.

    Passing that through would put "unreachable" and "perfectly ungrounded"
    behind the same falsy value, in front of a caller whose next statement is
    arithmetic.
    """
    _ExecutionContext.set_ext(_GroundednessExt(result=None))
    with pytest.raises(ServiceError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.GROUNDEDNESS, text="t", grounding_sources=["s"])
    assert "not a score of zero" in str(excinfo.value)


async def test_groundedness_takes_no_model(cleared_context):
    """The deployment is the platform's, resolved from ``ai_foundry_models``.

    Accepting ``model=`` would discard it silently three hops downstream -- the
    platform data service reads the catalog row, not the request -- so it is refused
    at the last hop that still knows why.
    """
    _ExecutionContext.set_ext(_GroundednessExt())
    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(
            perceivo.types.AIService.GROUNDEDNESS,
            model="gpt-5.2",
            text="t",
            grounding_sources=["s"],
        )
    assert "takes no model" in str(excinfo.value)


async def test_groundedness_outside_a_run_refuses_rather_than_mocking(
    clean_config, cleared_context
):
    """No bring-your-own-key equivalent exists, so there is nothing to fall to.

    ``CHAT`` outside a run resolves to the mock or to a developer key. Content
    Safety is the platform's own resource under the platform's managed identity,
    and the mock has no groundedness answer to give -- so the standalone runtime
    must refuse instead of returning a number a scorer would multiply.
    """
    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke(perceivo.types.AIService.GROUNDEDNESS, text="t", grounding_sources=["s"])
    assert "outside a run" in str(excinfo.value)


async def test_a_bare_string_service_works(clean_config, cleared_context):
    """``AIService`` is a ``StrEnum``, so a literal is always accepted."""
    response = await _AI.invoke("chat", model="m", messages=[])
    assert response["id"] == "mock-llm-completion"


async def test_an_unknown_service_is_a_perceivo_error(clean_config, cleared_context):
    """``AIService("nope")`` raises a bare ``ValueError``, which escapes
    ``except PerceivoError`` — the single promise ``types.errors`` makes."""
    with pytest.raises(ConfigurationError) as excinfo:
        await _AI.invoke("not-a-capability")
    assert "content_safety" in str(excinfo.value), "the admissible values are listed"


# ---------------------------------------------------------------------------
# managed_service.database
# ---------------------------------------------------------------------------


async def test_connect_performs_no_io(cleared_context):
    """A handle, not a connection.

    Run with no execution context precisely because every platform call raises
    there: if ``connect`` resolved anything, this would raise instead of return.
    """
    client = await _DATABASE.connect("inst-1")
    assert client.instance_id == "inst-1"


async def test_query_sends_exactly_what_database_query_sends(stub_ext):
    """The preservation claim for the split, asserted as payload identity.

    ``database_query(id, sql)`` and ``(await connect(id)).query(sql)`` have to
    put the same four fields on the wire, or moving a call site from one to the
    other changes the request while looking like a rename.
    """
    seen: list[tuple] = []

    async def execute_database_query(database_id, sql, read_only, max_rows):
        seen.append((database_id, sql, read_only, max_rows))
        return {"columns": ["id"], "rows": [{"id": 1}], "row_count": 1}

    stub_ext.execute_database_query = execute_database_query

    client = await _DATABASE.connect("inst-1")
    through_plane = await client.query("SELECT 1")
    through_backend = await _backend.database_query("inst-1", "SELECT 1")

    assert seen[0] == seen[1] == ("inst-1", "SELECT 1", True, 1000)
    assert through_plane == through_backend


async def test_the_per_call_knobs_survive_onto_query(stub_ext):
    """``read_only`` and ``max_rows`` are per-call, not bound at ``connect``.

    Both go on the wire on every request today, so an asset that varies them
    between two queries on the same database must keep being able to.
    """
    seen: list[tuple] = []

    async def execute_database_query(database_id, sql, read_only, max_rows):
        seen.append((database_id, sql, read_only, max_rows))
        return {}

    stub_ext.execute_database_query = execute_database_query
    client = await _DATABASE.connect("inst-1")
    await client.query("UPDATE claims SET status = 'closed'", read_only=False, max_rows=5)
    await client.query("SELECT 1")

    assert seen == [
        ("inst-1", "UPDATE claims SET status = 'closed'", False, 5),
        ("inst-1", "SELECT 1", True, 1000),
    ]


async def test_bind_parameters_are_refused_rather_than_rerouted(stub_ext):
    """The design's example passes them; ``database_query`` has no channel for them.

    The dangerous alternative is forwarding to ``external_database_query``,
    which does — a different endpoint against a different kind of connection,
    selected by the presence of an extra argument and visible nowhere at the
    call site.
    """
    calls: list[tuple] = []

    async def execute_database_query(*args):
        calls.append(args)
        return {}

    async def execute_external_database_query(*args):
        calls.append(args)
        return {}

    stub_ext.execute_database_query = execute_database_query
    stub_ext.execute_external_database_query = execute_external_database_query

    client = await _DATABASE.connect("inst-1")
    with pytest.raises(NotImplementedError) as excinfo:
        await client.query("SELECT * FROM claims WHERE status = $1", "open")

    assert "external_database_query" in str(excinfo.value)
    assert calls == [], "a refused call must not have reached a backend"


async def test_the_refusal_does_not_send_the_caller_to_a_removed_function(stub_ext):
    """The message may *mention* a name that does not exist; it may not *call* one.

    Advice such as ``Use perceivo.infra.external_database_query(connection_id,
    sql, params=[...])`` names a namespace the SDK does not have — the suggested
    call raises ``AttributeError``, so the one caller who followed the advice
    would get a second, less legible failure than the one they started with.

    The check is on ``perceivo.X(`` and not on ``perceivo.X``, deliberately: a
    name followed by an open paren is advice to call something, and advice has
    to resolve.
    """
    client = await _DATABASE.connect("inst-1")
    with pytest.raises(NotImplementedError) as excinfo:
        await client.query("SELECT * FROM claims WHERE status = $1", "open")
    message = str(excinfo.value)

    called = re.findall(r"perceivo(?:\.[A-Za-z_][A-Za-z0-9_]*)+(?=\()", message)
    for name in called:
        target, resolved = perceivo, True
        for part in name.split(".")[1:]:
            try:
                target = getattr(target, part)
            except AttributeError:
                resolved = False
                break
        assert resolved, (
            f"the refusal tells the caller to call {name}(), which does not "
            f"resolve on the public surface"
        )

    assert "has no public" in message, (
        "the refusal must say the parameterised endpoint has no public "
        "spelling; without that the reader is left assuming one exists and "
        "that they simply have not found it"
    )


async def test_query_outside_a_run_raises_what_the_backend_raises(cleared_context):
    """Preservation in the other direction: ``database_query`` errors outside a
    run, so this does too. A member that starts succeeding fails the
    preservation check as hard as one that starts failing."""
    client = await _DATABASE.connect("inst-1")
    with pytest.raises(RuntimeError) as plane_exc:
        await client.query("SELECT 1")
    with pytest.raises(RuntimeError) as backend_exc:
        await _backend.database_query("inst-1", "SELECT 1")
    assert type(plane_exc.value) is type(backend_exc.value)


# ---------------------------------------------------------------------------
# The object segment is not callable, and plane hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("handle", ["ai", "database"])
def test_the_object_segment_is_not_callable(handle: str) -> None:
    """The object segment must not be callable.

    It binds harder here than under ``cluster``: ``database_query`` *is* a
    callable verb, so the obvious mapping — bind that name as the segment —
    would produce a callable object segment and violate the rule.
    """
    obj = getattr(perceivo.managed_service, handle)
    assert not callable(obj)


def test_managed_service_itself_is_not_callable() -> None:
    assert not callable(perceivo.managed_service)


#: The local plane: the modules a runner's eager import graph must not contain.
_LOCAL_PLANE = frozenset(
    {"component", "orchestration", "validator", "runner", "local", "config", "bootstrap"}
)


def _plane_of(dotted: str) -> str:
    """``perceivo.local.mocks.llm`` -> ``local``; ``httpx`` -> ``httpx``."""
    parts = dotted.split(".")
    if parts[0] == "perceivo" and len(parts) > 1:
        return parts[1]
    return parts[0]


def _module_scope_imports(path: pathlib.Path) -> set[str]:
    """Every plane imported at *module* scope by *path*, absolute or relative.

    A relative import of depth >= 2 (``from ..local.mocks.llm import MockLLM``)
    leaves the subpackage, so its first segment is a sibling plane. Depth 1
    stays inside ``managed_service`` and is not one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(_plane_of(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 1:
                continue  # stays inside managed_service
            if node.level == 0 and module == "perceivo":
                roots.update(alias.name for alias in node.names)  # from perceivo import local
            elif node.level == 0:
                roots.add(_plane_of(module))
            elif module:
                roots.add(module.split(".")[0])  # from ..local.mocks.llm import ...
            else:
                roots.update(alias.name for alias in node.names)  # from .. import local
    return roots


@pytest.mark.parametrize("module_name", ["__init__", "ai", "database", "_ai_standalone"])
def test_the_plane_does_not_pull_in_section_11_at_import_time(module_name: str) -> None:
    """``perceivo/__init__.py`` MUST NOT eagerly import any local-plane module,
    and ``managed_service`` is eagerly imported.

    The plane genuinely needs ``perceivo.config`` and ``perceivo.local`` — they
    are where the mock and the provider transport live, and reimplementing
    either inside a platform plane would be worse than importing them. Both are taken
    as function-local imports on the outside-a-run path, which is unreachable
    inside a runner, so the local plane stays out of the eager graph.

    ``_ai_standalone`` is parametrised here because that path lives
    outside ``ai.py`` and the two local-plane imports live with it. Dropping it would
    leave this control asserting only about files that do not contain the
    thing it guards — passing, and measuring nothing.

    Checked against the module source rather than ``sys.modules``, because the
    runtime check cannot distinguish this module's fault from an existing gap
    in the same control: ``perceivo/__init__.py`` still lists ``local`` and ``config``
    on its own eager import line, so ``perceivo.local`` is in ``sys.modules``
    before this package is reached. When that line is fixed, this test keeps
    working and the runtime check becomes available as a second layer.
    """
    path = pathlib.Path(perceivo.managed_service.__file__).parent / f"{module_name}.py"
    offending = _module_scope_imports(path) & _LOCAL_PLANE
    assert not offending, (
        f"perceivo/managed_service/{module_name}.py imports {sorted(offending)} at "
        f"module scope. managed_service is eagerly imported by perceivo/__init__.py, "
        f"so this drags the local plane into every runner's module graph through a "
        f"public plane. Move it into the function."
    )


def test_the_import_scanner_can_see_a_violation() -> None:
    """A scanner that reports clean on a file it cannot parse is not a control.

    ``perceivo/local/context.py`` opens with ``from ..config import ...`` — the
    exact relative form ``ai.py`` would use to leak the local plane into the
    eager graph. If
    the walk cannot see it there, its silence on ``ai.py`` means nothing.
    """
    path = pathlib.Path(perceivo.__file__).parent / "local" / "context.py"
    assert "config" in _module_scope_imports(path)
