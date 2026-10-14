"""The deferred public members, and how the ``files`` plane is partitioned.

Two slices of the deferred public surface, both of which are held by an
*absence* and are therefore the two that a name-only conformance check cannot
check:

1. **Nothing that could not be built was stubbed.** The deferred members are
   asserted absent, so a later "harmless" ``NotImplementedError`` placeholder
   cannot pass for delivery — it would satisfy the name conformance check while
   delivering nothing. Includes the two module/verb shadowing collisions the
   deferral leaves behind (``metrics.query``) and the cluster verbs with no
   backend.
2. **``files`` splits on how a file is addressed.** ``files.platform`` is
   read-only because it has no write verb at all, and its container argument is
   a closed set checked before the call rather than a comment about a set.

The properties asserted on the members that *were* built — rename identity,
verbatim default forwarding, argument forwarding without a fabricated ``total``,
and the ``bootstrap`` plane — live in
:mod:`tests.unit.test_public_plane_surface`.

**No credential appears in this file.**
"""

import inspect

import pytest

import perceivo
from perceivo.types.errors import ConfigurationError, PerceivoError

# ---------------------------------------------------------------------------
# 6. Nothing deferred was stubbed
# ---------------------------------------------------------------------------

#: The declared members that the platform cannot yet serve. Each is recorded in
#: ``tests/conformance/EXCEPTIONS.json`` with the enumerated
#: ExtensionManager-and-route evidence. The count is cited rather than restated
#: because the ledger can be recounted and a numeral in prose cannot. They must
#: be *absent*: a placeholder that raises ``NotImplementedError`` would satisfy
#: the name conformance check while delivering nothing, which is the one outcome
#: the exceptions file exists to prevent.
_DEFERRED = [
    ("cluster", "list"),
    ("cost", "query"),
    ("cost", "summary"),
    ("files", "stat"),
    ("metrics", "query"),
    ("reviews", "get"),
    ("reviews", "submit"),
    ("stream", "captured"),
    ("stream", "captured_text"),
]


@pytest.mark.parametrize(("plane", "member"), _DEFERRED, ids=[f"{p}.{m}" for p, m in _DEFERRED])
def test_a_deferred_member_is_absent_rather_than_stubbed(plane, member):
    module = getattr(perceivo, plane)
    assert member not in module.__all__, (
        f"perceivo.{plane}.{member} is on the exported surface. It is recorded in "
        f"EXCEPTIONS.json as undeliverable; if it became deliverable, remove the "
        f"exception rather than leaving both."
    )
    existing = getattr(module, member, None)
    assert not callable(existing), (
        f"perceivo.{plane}.{member} is callable but is not exported — either it "
        f"is a stub, or it is about to be exported by accident"
    )


def test_the_metrics_plane_exports_nothing_and_says_so():
    """`metrics` declares one verb and its `query` is recorded as deferred, so
    the honest export list is empty.

    Empty rather than absent matters: the surface lock reads ``__all__`` in
    preference to ``dir()``, so a module with no ``__all__`` would put every
    incidental attribute on the surface. The six ``get_*`` readers that
    `query` replaces are deleted, not moved.
    """
    assert perceivo.metrics.__all__ == []
    for gone in (
        "get_billing_usage",
        "get_capability_summary",
        "get_intelligence_summary",
        "get_orchestration_summary",
        "get_review_stats",
        "get_runs_over_time",
    ):
        assert not hasattr(perceivo.metrics, gone), f"metrics.{gone} is still reachable"
    with pytest.raises(ModuleNotFoundError):
        __import__("perceivo.metrics.query")


def test_the_deferred_cluster_verbs_are_absent_from_every_object():
    """Six verbs are declared per object; only ``connect`` exists. ``logs`` is
    the one to drop rather than schedule: infrastructure pod logs
    are refused by policy: the platform serves no pod logs for redis, milvus,
    elasticsearch, mcp, ray or its own pods."""
    for name in ("redis", "milvus", "elasticsearch"):
        obj = getattr(perceivo.cluster, name)
        for verb in ("status", "start", "stop", "restart", "logs"):
            assert not hasattr(obj, verb), f"cluster.{name}.{verb} exists but has no backend"


def test_the_intelligence_members_that_are_still_deferred_are_absent():
    """``features`` ships; ``inference.status`` does not.

    ``features`` is pinned here by its exact export list. What it serves is a
    deliberate decision -- a store keyed by view *and* entity -- so a plane that
    grows a verb without such a decision is the defect this list makes fail.

    ``inference.status`` keeps the original assertion unchanged: it is still
    declared, still unbuilt, and still the only ``intelligence`` member in
    ``EXCEPTIONS.json``.
    """
    assert perceivo.intelligence.features.__all__ == [
        "FeatureView",
        "Materialization",
        "get_online_records",
        "get_records",
        "list_views",
        "materialize",
    ]
    assert not hasattr(perceivo.intelligence.inference, "status")
    assert "status" not in perceivo.intelligence.inference.__all__


# ---------------------------------------------------------------------------
# `files` splits on how a file is addressed
# ---------------------------------------------------------------------------


def test_the_platform_namespace_has_no_write_verb():
    """This is the whole read-only guarantee, and it is structural.

    Keeping ``container=`` off ``files.write`` would hold the system containers
    read-only by the *absence* of a parameter, which survives only as long as
    nobody adds one back for symmetry. This namespace holds it by the absence of
    a verb. Pinned so that adding one is a test failure rather
    than a review comment nobody makes.
    """
    for verb in ("write", "write_text", "mkdir", "move", "delete", "rename", "create_folder"):
        assert not hasattr(perceivo.files.platform, verb), (
            f"files.platform.{verb} exists — the read-only guarantee is "
            f"that this namespace has no write verb at all"
        )
    assert sorted(perceivo.files.platform.__all__) == ["list", "read"]


def test_the_working_container_is_not_a_member_of_the_closed_set():
    """``working`` is writable, so admitting it to the enum would force
    ``write`` to take a container — at which point ``ORCHESTRATIONS`` is
    writeable-looking again and the closed set buys nothing. It is addressed by
    :mod:`perceivo.files.working` instead of named by a member."""
    assert "working" not in {c.value for c in perceivo.files.StorageContainer}
    assert not hasattr(perceivo.files.StorageContainer, "WORKING")


@pytest.mark.parametrize("bad", ["working", "org", "workng", "", "ORG"])
def test_an_unknown_container_is_refused_before_the_call(bad):
    """A closed set that is not checked is a comment about a set.

    ``"workng"`` would reach the platform as a container name and come back as
    an empty listing or a 404 — a wrong answer, not an error. ``"working"`` and
    ``"org"`` name other ``files`` namespaces, so they are the two most likely
    to be passed by mistake.
    """
    with pytest.raises(ConfigurationError) as exc:
        perceivo.files.platform._container(bad)
    assert "perceivo.files.working" in str(exc.value)
    assert "perceivo.files" in str(exc.value)


def test_the_refusal_is_a_perceivo_error_not_a_value_error():
    """``Container(x)`` raises ``ValueError``, which escapes ``except
    PerceivoError`` — so a mistyped container in a component with correct error
    handling would surface as an unhandled exception."""
    with pytest.raises(PerceivoError):
        perceivo.files.platform._container("nope")


@pytest.mark.parametrize("member", list(perceivo.files.StorageContainer))
def test_every_member_of_the_closed_set_is_accepted_by_both_spellings(member):
    """Both spellings, because ``Container`` is a ``StrEnum`` precisely so a
    caller who has not adopted the enum can pass the string."""
    assert perceivo.files.platform._container(member) == member.value
    assert perceivo.files.platform._container(member.value) == member.value


def test_list_blobs_is_reached_from_two_namespaces_and_neither_is_redundant():
    """The reconciliation: five names over ``files_service``'s four
    methods, and no method left without a name. ``list_blobs`` is the one
    reached twice — with the container fixed for the run's scratch, and with it
    passed for the three platform containers. Dropping either would delete a
    capability that exists on the run path today."""
    assert not hasattr(perceivo.files, "list_blobs")
    assert not hasattr(perceivo.files, "read_blob")
    assert callable(perceivo.files.working.list)
    assert callable(perceivo.files.platform.list)
    # `working.list` takes no container: this namespace *is* the container.
    assert "container" not in inspect.signature(perceivo.files.working.list).parameters
    assert "container" in inspect.signature(perceivo.files.platform.list).parameters


def test_the_working_namespace_declares_its_exports():
    """Without ``__all__`` a module's exported surface is ``dir()``, which
    carries ``Any`` and ``annotations`` — typing imports no caller should reach,
    and names the name conformance check correctly reports as undeclared."""
    assert perceivo.files.working.__all__ == ["list", "read", "write"]
    assert "Any" not in perceivo.files.working.__all__
