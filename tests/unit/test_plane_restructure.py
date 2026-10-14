"""The planes forward to their implementations, byte for byte.

The planes must **preserve** behaviour: every asset must produce the same run
record through a plane as through the implementation behind it. A status change
in either direction is a failure, red-turning-green exactly as much as
green-turning-red.

The planes earn that by re-exporting function *objects* rather than writing new
functions that do the same thing. So the tests that matter here
are not behavioural round-trips -- they are **identity** assertions. If
``perceivo.params.get is perceivo.params._variables.get``, there is no behaviour
left to drift, and no reviewer has to read two implementations and decide
whether they agree.

The function objects live at private module paths, and the identity table
below names those paths: a reimplementation behind a public name fails it, and
it pins where the object comes from.

Two members cannot be pure re-exports, and both are pinned here against the real
local runtime rather than a mock, because the claim being made is specifically
about what ``LocalExtensionManager`` does.
"""

from __future__ import annotations

import inspect

import pytest

import perceivo
from perceivo._internal.context import _ExecutionContext
from perceivo.config import Config
from perceivo.local.context import LocalExtensionManager

from .fixtures_component_reach import reachable_from_bare_import

# ---------------------------------------------------------------------------
# Identity: the public name and the implementation are the same object
# ---------------------------------------------------------------------------

# (public dotted path, implementation dotted path) for every restructured-plane
# member whose identity with its implementation this module asserts. The `files`
# and `managed_service` planes are deliberately not in this table; they have
# their own suites — `test_files_plane.py` and `test_managed_service_*.py`.
#
# The three `cluster` rows name the private accessors in
# `perceivo.cluster._backend`: the parametrisation is what proves
# `cluster.<svc>` forwards rather than reimplements.
REEXPORTS = [
    ("params.get", "params._variables.get"),
    ("params.all", "params._variables.all"),
    ("params.keys", "params._variables.keys"),
    ("params.declaration", "params._variables.declaration"),
    ("alerts.send", "alerts._client.send"),
    ("alerts.send_direct", "alerts._direct.send_direct"),
    ("stream.token", "stream._channel.token"),
    ("cluster.milvus.connect", "cluster._backend.get_milvus"),
    ("cluster.redis.connect", "cluster._backend.get_redis"),
    ("cluster.elasticsearch.connect", "cluster._backend.get_elasticsearch"),
    ("intelligence.inference.predict", "intelligence.inference._client.predict"),
]


def _resolve(path: str):
    obj = perceivo
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


@pytest.mark.parametrize(("public", "impl"), REEXPORTS)
def test_public_member_is_the_same_object_as_its_implementation(public: str, impl: str) -> None:
    """Not "behaves like" -- *is*. That is what makes preservation mechanical."""
    assert _resolve(public) is _resolve(impl), (
        f"perceivo.{public} is not perceivo.{impl}. If this member had to be "
        f"reimplemented, preservation stops being structural and has to be "
        f"argued from the diff instead."
    )


def test_every_reexport_target_still_exists() -> None:
    """Guards the direction the identity test cannot see.

    ``getattr`` on a missing implementation path raises ``AttributeError``
    inside ``_resolve`` -- a private module having moved or been deleted out
    from under a plane that still forwards to it. Named separately so the
    failure says which direction broke.
    """
    for _public, impl in REEXPORTS:
        _resolve(impl)


# ---------------------------------------------------------------------------
# The object segment MUST NOT be callable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["milvus", "redis", "elasticsearch"])
def test_cluster_handle_is_not_callable(service: str) -> None:
    """`perceivo.cluster.milvus` is a handle, not a function.

    The whole reason the public path is ``cluster.milvus.connect`` rather than
    ``cluster.milvus`` is that an object segment of a public path must never be
    callable.
    """
    handle = getattr(perceivo.cluster, service)
    assert not callable(handle)
    assert callable(handle.connect)


def test_mcp_is_a_cluster_handle_and_the_top_level_plane_is_gone() -> None:
    """``perceivo.cluster.mcp.connect()`` is MCP's one spelling.

    The platform deploys MCP per organization exactly as it deploys redis,
    milvus and elasticsearch, so it is reached the same way. The top-level
    ``perceivo.mcp`` plane is deleted with no alias. Checked from a bare
    import, because a component reaches the SDK only by attribute.
    """
    assert hasattr(perceivo.cluster, "mcp")
    assert reachable_from_bare_import("cluster.mcp.connect")["cluster.mcp.connect"]
    assert not reachable_from_bare_import("mcp.call_tool")["mcp.call_tool"]


# ---------------------------------------------------------------------------
# The one shape change: sync -> async on `stream.active`
# ---------------------------------------------------------------------------


def test_stream_active_is_async_and_the_function_it_wraps_is_not() -> None:
    """The public member is ``await perceivo.stream.active()``; the function it
    wraps is ``def``. Pinned so the divergence is deliberate and visible.

    ``stream.active`` is the one member of these planes that is NOT a pure
    re-export, so it is the one place a reader cannot infer preservation from
    identity. Asserting both halves keeps the shape change legible.
    """
    assert inspect.iscoroutinefunction(perceivo.stream.active)
    assert not inspect.iscoroutinefunction(perceivo.stream._channel.is_active)


@pytest.mark.asyncio
async def test_stream_active_forwards_the_value_unchanged() -> None:
    class _Ext:
        def conversation_is_active(self) -> bool:
            return True

        async def conversation_stream_token(self, text: str) -> None: ...

    _ExecutionContext.set_ext(_Ext())
    try:
        assert await perceivo.stream.active() is True
    finally:
        _ExecutionContext.clear_all()


def test_dropping_the_await_is_silently_wrong() -> None:
    """The hazard the ``review_required`` flag exists for, made visible.

    A coroutine object is always truthy, so ``if perceivo.stream.active():``
    takes the true branch even when no channel is attached -- no error, no
    warning. This asserts the hazard is real so that the codemod's obligation
    to insert ``await`` as an AST edit is anchored to a failing property rather
    than to a comment.
    """
    coro = perceivo.stream.active()
    try:
        assert bool(coro) is True  # truthy with no context set at all
    finally:
        coro.close()


# ---------------------------------------------------------------------------
# Preservation: the members that raise today must keep raising
# ---------------------------------------------------------------------------


@pytest.fixture
def local_runtime():
    """The real ``LocalExtensionManager``, not a mock.

    The claim under test is about what the local runtime actually does, so a
    mock with a hand-chosen ``spec`` would be asserting the fixture rather than
    the runtime.
    """
    _ExecutionContext.clear_all()
    _ExecutionContext.set_ext(LocalExtensionManager(Config()))
    yield
    _ExecutionContext.clear_all()


@pytest.mark.asyncio
async def test_stream_token_returns_none_under_the_local_runtime(local_runtime) -> None:
    assert await perceivo.stream.token("hello") is None  # type: ignore[func-returns-value]


@pytest.mark.asyncio
async def test_inference_predict_is_served_by_the_local_mock(local_runtime) -> None:
    """``predict_inference`` is served locally by the inference mock.

    The local runner needs the classifier-router corpus to run, so the call
    returns the mock's seeded prediction.

    One arm: ``intelligence.inference.predict`` **is** ``_client.predict`` -- the
    identity row in ``REEXPORTS`` proves it.
    """
    result = await perceivo.intelligence.inference.predict("intel-1", {"x": 1})
    assert result.model_id == "mock-model"
    assert result.prediction["result"]["predicted_orchestration_id"] == "mock-orchestration"


# ---------------------------------------------------------------------------
# Severity — the declared enum
# ---------------------------------------------------------------------------


def test_severity_admits_exactly_what_send_admits() -> None:
    """Read off the validator, not off the prose.

    If either alert route ever grows or drops a severity, this fails rather
    than letting the enum drift into advertising a value a send rejects.
    """
    from perceivo.alerts._client import _VALID_SEVERITIES as _ADMIN
    from perceivo.alerts._direct import _VALID_SEVERITIES as _DIRECT

    members = {member.value for member in perceivo.types.Severity}
    assert members == set(_ADMIN) == set(_DIRECT)


def test_severity_is_a_str_enum_so_bare_strings_keep_working() -> None:
    assert perceivo.types.Severity.INFO == "info"
    assert perceivo.types.Severity.WARNING == "warning"
    assert perceivo.types.Severity.CRITICAL == "critical"
