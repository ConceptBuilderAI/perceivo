"""The public plane members that are built, and the ones that are not.

The name conformance check compares **names only** — never a signature, never a
return type, never a forwarded default. That is the right scope for a
conformance check and it is also why a change driven by it is dangerous on its
own: new dotted names turn the check green whether or not any of them calls what
it is declared to call. A verb built against a route that does not exist is
worse than an absent verb, because the check then reads green. This file holds
what the name check cannot.

Four properties, each guarding against a known class of defect:

1. **A rename is the same function object, not a copy.** ``perceivo.stream.send
   is perceivo.stream._channel.send`` is a stronger statement than "they agree
   today": the public name binds the identical function object, which is what
   lets an asset produce the same run record through either path.
   A reimplementation at the public path would satisfy every other assertion in
   this file and fail this one.
2. **A wrapper forwards its backend's defaults verbatim.** A transcribed default
   is how a forwarded call quietly reads a different number of rows. Asserted
   with :func:`inspect.signature` against the backend, not read off the source.
3. **A ``Page`` wrapper does not fabricate ``total``.** ``Page.of`` reports
   ``total == len(data)``, which is only true of a complete result; every wrapper
   here is over a ``limit``-truncated call, so ``total`` must stay ``0`` —
   ``Page``'s "no total reported".
4. **Nothing that could not be built was stubbed.** The deferred members
   are asserted *absent*, so a later "harmless" ``NotImplementedError`` placeholder
   cannot pass for delivery.

**No credential appears in this file.** ``bootstrap.start`` is exercised with its
backing ``connect`` monkeypatched, so no token, URL or organization id is ever
constructed, defaulted or read from the environment.

This module holds properties 1-3 — the renames, the forwarded defaults, the
forwarded arguments and the unfabricated ``total``, the ``bootstrap`` plane, and
``runner.history``'s deterministic shadowing. Property 4, and the ``files``
namespace split that is held the same way, are asserted in
:mod:`tests.unit.test_new_surface_deferred_and_files_namespaces`.
"""

from __future__ import annotations

import inspect
import re
import sys

import pytest

import perceivo
from perceivo.bootstrap import _session
from perceivo.runner.history import RunHistory as _RunHistory
from perceivo.stream import _channel as channel
from perceivo.types.errors import ConfigurationError, ContextError, PerceivoError
from perceivo.types.pagination import Page

# ---------------------------------------------------------------------------
# 1. Renames bind the same object
# ---------------------------------------------------------------------------

#: ``(public name, the object it must be)``. Every one of these is a declared
#: public name, and the third column is the **implementation** it is bound to.
#:
#: An asset must produce the same run record through the public name as through
#: the implementation, and what guarantees that is the declared name resolving
#: to the identical function object -- not to a re-implementation at a public
#: path that agrees with it today. A private module is the right place for the
#: object to live.
_ALIASES = [
    (
        "cost.model_pricing",
        perceivo.cost.model_pricing,
        perceivo.cost._recorder.model_pricing,
    ),
    ("cost.record", perceivo.cost.record, perceivo.cost._recorder.record),
    ("files.mkdir", perceivo.files.mkdir, perceivo.files._org.mkdir),
    ("stream.send", perceivo.stream.send, channel.send),
    ("stream.receive", perceivo.stream.receive, channel.receive),
    ("stream.peek", perceivo.stream.peek, channel.peek),
    ("stream.drain", perceivo.stream.drain, channel.drain),
    ("stream.token", perceivo.stream.token, channel.token),
]


@pytest.mark.parametrize(("name", "new", "backend"), _ALIASES, ids=[a[0] for a in _ALIASES])
def test_a_rename_is_the_same_function_object(name, new, backend):
    """Not "equal", not "same behaviour" — the identical object.

    Anything weaker permits the two to be maintained separately.
    """
    assert new is backend, f"{name} is a copy of its backend, not the backend"


def test_cost_model_pricing_did_not_drop_the_provider_argument():
    """A one-argument ``model_pricing(model="gpt-4o")`` signature would be wrong.

    The backing call is ``ExtensionManager.get_llm_pricing(provider, model_name)``
    over ``GET /internal/llm-models/pricing/{provider}/{model_name}``
    — the provider is a path segment. Pinning the two-argument shape here is what
    stops someone closing a declared-name gap by adding a one-argument wrapper
    that guesses the provider from the model string.
    """
    assert list(inspect.signature(perceivo.cost.model_pricing).parameters) == [
        "provider",
        "model_name",
    ]


# ---------------------------------------------------------------------------
# 2. Wrappers forward their backend's defaults
# ---------------------------------------------------------------------------


def test_reviews_query_forwards_get_results_defaults_verbatim():
    """The shared parameters only — ``query`` does not mirror the id fields.

    ``reviews.query`` takes a ``subject=``/``subject_id=`` grammar and maps it
    onto the route's three named ids inside the SDK, so the signatures
    deliberately differ. What must **not** differ is any
    default the two share: a transcribed ``limit`` or ``status`` is how a
    forwarded call quietly reads a different number of rows, and that hazard is
    unchanged by the grammar.
    """
    backend = inspect.signature(perceivo.reviews._results.get_results).parameters
    wrapper = inspect.signature(perceivo.reviews.query).parameters
    shared = [key for key in backend if key in wrapper]
    assert shared == ["status", "limit"], (
        "the parameters query shares with get_results changed; if that is "
        "intended, say which and why here"
    )
    for key in shared:
        assert wrapper[key].default == backend[key].default, key
    assert list(wrapper)[:2] == ["subject", "subject_id"], (
        "the subject grammar is declared first; a caller passing them "
        "positionally must not silently land on status/limit"
    )


def test_the_subject_map_is_read_off_the_backend_not_written_out():
    """The property that separates a derivation from a transcription.

    Narrows ``get_results`` to one subject id and asserts the map follows with
    no edit to the reviews package: a *removed* id stops being accepted rather
    than being accepted and discarded. A hand-written map would not follow.
    """
    import importlib

    from perceivo.reviews import ReviewSubject as Subject

    reviews = sys.modules["perceivo.reviews"]
    # ``perceivo.reviews._results`` is the implementation submodule. It resolves
    # as itself because the plane exports only ``query`` -- no alias rebinds it.
    results_module = perceivo.reviews._results
    original = results_module.get_results

    async def get_results(  # pragma: no cover - never called
        component_id=None, status="COMPLETED", limit=50
    ):
        raise AssertionError("the map must be derived from the signature alone")

    try:
        results_module.get_results = get_results
        extended = importlib.reload(reviews)
        assert extended._SUBJECT_IDS == {Subject.COMPONENT: "component_id"}
    finally:
        results_module.get_results = original
        importlib.reload(reviews)

    assert perceivo.reviews._SUBJECT_IDS == {
        Subject.COMPONENT: "component_id",
        Subject.ORCHESTRATION: "orchestration_id",
        Subject.CAPABILITY: "capability_id",
    }


def test_stream_history_forwards_get_history_defaults_verbatim():
    backend = inspect.signature(channel.get_history).parameters
    wrapper = inspect.signature(perceivo.stream.history).parameters
    assert list(wrapper) == list(backend)
    for key in backend:
        assert wrapper[key].default == backend[key].default, key


def test_runner_history_forwards_list_runs_default_limit():
    backend = inspect.signature(_RunHistory.list_runs).parameters
    assert perceivo.runner.history.__defaults__[0] == backend["limit"].default


def test_bootstrap_start_takes_connect_parameters_and_nothing_else():
    """The declared signature and ``connect``'s are the same one, which is the
    whole evidence that this plane is a rename rather than a build."""
    backend = inspect.signature(_session.connect).parameters
    wrapper = inspect.signature(perceivo.bootstrap.start).parameters
    assert list(wrapper) == list(backend)
    for key in backend:
        assert wrapper[key].default == backend[key].default, key
        assert wrapper[key].kind is inspect.Parameter.KEYWORD_ONLY, key


# ---------------------------------------------------------------------------
# 3. Wrappers forward arguments unchanged, and do not fabricate a total
# ---------------------------------------------------------------------------


async def test_reviews_query_forwards_every_argument_and_reports_no_total(monkeypatch):
    from perceivo.reviews import ReviewSubject as Subject

    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return [{"id": "a"}, {"id": "b"}]

    monkeypatch.setattr(perceivo.reviews, "_get_results", fake)
    page = await perceivo.reviews.query(
        subject=Subject.COMPONENT, subject_id="c", status="PENDING", limit=7
    )
    assert seen == {"component_id": "c", "status": "PENDING", "limit": 7}, (
        "the subject pair must arrive as exactly one named id, and the ids the "
        "caller did not scope by must be absent rather than sent as None"
    )
    assert isinstance(page, Page)
    assert list(page) == [{"id": "a"}, {"id": "b"}]
    assert page.page == 1
    assert page.page_size == 7
    assert page.total == 0, "a fabricated total over a limit-truncated call reads as authoritative"
    assert page.has_next is False


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("component", "component_id"),
        ("orchestration", "orchestration_id"),
        ("capability", "capability_id"),
    ],
)
async def test_every_reachable_subject_maps_to_its_own_id(monkeypatch, subject, expected):
    """One subject, one id, and never a second one."""
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(perceivo.reviews, "_get_results", fake)
    await perceivo.reviews.query(subject=subject, subject_id="x")
    assert seen == {expected: "x", "status": "COMPLETED", "limit": 50}


async def test_an_unscoped_query_is_allowed_and_sends_no_id(monkeypatch):
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(perceivo.reviews, "_get_results", fake)
    await perceivo.reviews.query()
    assert seen == {"status": "COMPLETED", "limit": 50}


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"subject": "component"}, "without subject_id="),
        ({"subject_id": "x"}, "without subject="),
        ({"subject": "widget", "subject_id": "x"}, "not a ReviewSubject value"),
        ({"subject": "organization", "subject_id": "x"}, "not a ReviewSubject value"),
        ({"subject": "intelligence", "subject_id": "x"}, "not a ReviewSubject value"),
        ({"subject": "node", "subject_id": "x"}, "not a ReviewSubject value"),
    ],
)
async def test_a_subject_the_route_cannot_filter_on_raises_rather_than_widens(
    monkeypatch, kwargs, fragment
):
    """Every quiet alternative returns someone else's reviews under a filter the
    caller believes was applied.

    Half a pair widens the query to everything visible, and a subject kind the
    route has no id for would drop the filter entirely; ``ReviewSubject`` holds
    only the three kinds the route filters on, so any other value is refused. The platform is never called: the guard runs first, which
    is asserted by the backend raising if it is reached.

    Caught as ``PerceivoError`` rather than as ``ConfigurationError`` so the test
    pins the SDK's promise — *catch the base and you catch everything* — and not
    merely the concrete class. ``Subject("widget")`` raises a bare ``ValueError``
    natively, which would escape that base; re-raising is what stops it.
    """

    async def never(**kwargs):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("the platform was called with an unusable filter")

    monkeypatch.setattr(perceivo.reviews, "_get_results", never)
    with pytest.raises(PerceivoError, match=re.escape(fragment)) as caught:
        await perceivo.reviews.query(**kwargs)
    assert isinstance(caught.value, ConfigurationError)
    # The bare builtin is what `tests/unit/test_error_hierarchy.py` budgets, and
    # a `raise ValueError` here would have spent three of that budget silently.
    assert not isinstance(caught.value, ValueError)


async def test_the_two_id_combination_the_grammar_cannot_express_is_still_reachable():
    """The subject grammar costs one filter combination, and the cost is pinned here.

    The route accepts ``component_id`` and ``orchestration_id`` together — "this
    review component, in this orchestration" — and one ``subject``/``subject_id``
    pair cannot say it. The implementation module still can, and is reached by
    its own import path rather than a second public spelling on the plane. If a
    later change removes that escape hatch, this fails and the loss is a decision
    rather than a rediscovered bug.
    """
    params = inspect.signature(perceivo.reviews._results.get_results).parameters
    assert {"component_id", "orchestration_id", "capability_id"} <= set(params)

    # The escape hatch is the submodule, not a plane export. Both halves matter:
    # ``results`` must still BE the module (an alias rebinding it is what made
    # this path raise AttributeError), and ``get_results`` must NOT be reachable
    # on the plane, because the plane declares ``query`` and nothing else.
    import types

    assert isinstance(perceivo.reviews._results, types.ModuleType)
    assert not hasattr(perceivo.reviews, "get_results")
    assert perceivo.reviews.__all__ == ["ReviewResult", "ReviewSubject", "query"]


async def test_stream_history_forwards_every_argument_and_reports_no_total(monkeypatch):
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return ["m1"]

    monkeypatch.setattr(perceivo.stream, "_get_history", fake)
    page = await perceivo.stream.history(limit=3, include_inbound=False)
    assert seen == {"limit": 3, "include_inbound": False}
    assert list(page) == ["m1"]
    assert (page.page, page.page_size, page.total) == (1, 3, 0)


async def test_runner_history_reads_the_projection_and_reports_the_total(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _RunHistory, "list_runs", lambda self, limit=20, offset=0: [{"run_id": "r"}] * 2
    )
    monkeypatch.setattr(_RunHistory, "count", lambda self: 7)
    page = await perceivo.runner.history(limit=2, base_dir=str(tmp_path))
    assert list(page) == [{"run_id": "r"}, {"run_id": "r"}]
    assert (page.page, page.page_size, page.total) == (1, 2, 7)


async def test_secrets_declaration_returns_what_get_declaration_returns(monkeypatch):
    """``declaration`` forwards to the vault function unchanged.

    The seam is ``perceivo.secrets._get_declaration``, not
    ``perceivo.secrets.get_declaration``: the plane binds the implementation
    privately, and ``declaration``
    resolves that private name as a module global at call time.
    """
    assert not hasattr(perceivo.secrets, "get_declaration"), (
        "the plane re-exports get_declaration; the public name is declaration, a clean "
        "break, so there is no shim and no alias"
    )
    monkeypatch.setattr(perceivo.secrets, "_get_declaration", lambda name: {"name": name})
    assert await perceivo.secrets.declaration("openai_api_key") == {"name": "openai_api_key"}


# ---------------------------------------------------------------------------
# 4. bootstrap — the three declared members
# ---------------------------------------------------------------------------


async def test_bootstrap_start_forwards_to_connect_without_inventing_anything(monkeypatch):
    """Every argument is passed through; nothing is defaulted, guessed or read
    from the environment by this wrapper. ``connect``'s own auto-detection is
    left to ``connect``."""
    seen = {}
    sentinel = object()

    monkeypatch.setattr(_session, "connect", lambda **kw: seen.update(kw) or sentinel)
    got = await perceivo.bootstrap.start(local=True)
    assert got is sentinel
    assert seen == {
        "egress_url": None,
        "execution_token": None,
        "organization_id": None,
        "config_path": None,
        "local": True,
    }


async def test_bootstrap_start_stringifies_a_path_config(monkeypatch, tmp_path):
    """``start`` declares ``str | Path``; ``connect`` is annotated ``str``. The
    conversion happens here so ``config.load`` — which does ``Path(path)``
    on whatever it is handed — never sees a widened contract it did not agree to.
    """
    seen = {}
    monkeypatch.setattr(_session, "connect", lambda **kw: seen.update(kw))
    await perceivo.bootstrap.start(config_path=tmp_path / "perceivo.yaml")
    assert seen["config_path"] == str(tmp_path / "perceivo.yaml")
    assert isinstance(seen["config_path"], str)


async def test_bootstrap_stop_refuses_a_session_that_is_not_active(monkeypatch):
    """The refusal is deliberate: the mechanism holds exactly one session, so
    serving the argument would either stop the wrong one or leave the global
    pointing at a dead object."""
    called = []
    monkeypatch.setattr(_session, "_active_session", object())
    monkeypatch.setattr(_session, "disconnect", lambda: called.append(1))

    with pytest.raises(ContextError):
        await perceivo.bootstrap.stop(session=object())
    assert called == [], "the refusal must not also stop the active session"


def test_the_refusal_is_catchable_as_a_perceivo_error():
    """Catching ``PerceivoError`` catches everything the SDK raises. A bare
    ``ValueError`` here would escape ``except PerceivoError`` and is counted by
    ``test_error_hierarchy.py``."""
    assert issubclass(ContextError, PerceivoError)


async def test_bootstrap_stop_with_no_argument_disconnects(monkeypatch):
    called = []
    monkeypatch.setattr(_session, "_active_session", None)
    monkeypatch.setattr(_session, "disconnect", lambda: called.append(1))
    await perceivo.bootstrap.stop()
    assert called == [1]


def test_bootstrap_session_reads_the_global_live_not_at_import_time(monkeypatch):
    """``from ._session import _active_session`` would bind ``None`` forever and
    report "no session" while one was active — a bug that looks like a working
    accessor in every test that connects before importing."""
    assert perceivo.bootstrap.session() is None
    marker = object()
    monkeypatch.setattr(_session, "_active_session", marker)
    assert perceivo.bootstrap.session() is marker


def test_bootstrap_exports_exactly_the_three_declared_members():
    """This plane declares three members. A ``Session`` alias here would put an
    undeclared name on the surface to fix a declared one."""
    assert perceivo.bootstrap.__all__ == ["session", "start", "stop"]
    assert "bootstrap" in perceivo.__all__


# ---------------------------------------------------------------------------
# 5. runner.history shadows its submodule deterministically
# ---------------------------------------------------------------------------


def test_runner_history_attribute_is_the_verb_not_the_module():
    assert inspect.iscoroutinefunction(perceivo.runner.history)


def test_the_submodule_is_still_reachable_by_module_path():
    """The shadowing must not break ``from perceivo.runner.history import
    RunHistory``, which ``perceivo/runner/stream.py`` depends on."""
    from perceivo.runner.history import RunHistory

    assert RunHistory is _RunHistory
    assert sys.modules["perceivo.runner.history"].RunHistory is RunHistory


def test_a_later_import_does_not_reinstate_the_module_binding():
    """The failure this pins is order-dependence, not the shadowing itself: if
    ``import perceivo.runner.history`` re-set the parent attribute, the surface
    would depend on what ran first."""
    import perceivo.runner.history

    assert inspect.iscoroutinefunction(perceivo.runner.history)
