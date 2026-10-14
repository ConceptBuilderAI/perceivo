"""Behaviour of the ``perceivo.local`` plane and the conversation/training
result types.

The plane answers three questions -- how a service is served, change it, and
what the run actually called -- both inside a run (from the live extension
manager) and outside one (from ``perceivo.yaml`` plus pending overrides). Each
branch is driven here and its answer asserted, so a plane that quietly answered
from the wrong source would fail.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import perceivo.local as local
from perceivo import config as cfg
from perceivo._internal.context import _ExecutionContext
from perceivo.local import _modes
from perceivo.local.mocks import trace as trace_mod
from perceivo.local.mocks.trace import MockCall
from perceivo.log.conversation import ContentPart, ConversationMessage
from perceivo.types.errors import ContextError
from perceivo.types.training import EvaluationResult, IterationResult, TrainingPlan


@pytest.fixture(autouse=True)
def _isolated_plane(monkeypatch, tmp_path):
    """No perceivo.yaml, no pending overrides, and the loaded-config global restored."""
    before = cfg._LOADED
    _modes.clear_overrides()
    monkeypatch.delenv("PERCEIVO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    yield
    _modes.clear_overrides()
    cfg._LOADED = before


class _FakeMocks:
    def __init__(self, modes: dict[str, str], calls: list[MockCall]):
        self._modes = modes
        self.synced = 0
        self.trace = SimpleNamespace(calls=calls)

    def mode(self, service: str) -> str:
        return self._modes[service]

    def sync_http(self) -> None:
        self.synced += 1


@pytest.fixture
def local_ext(cleared_context):
    calls = [MockCall("redis", "get", "mock"), MockCall("llm", "chat", "byo-key")]
    ext = SimpleNamespace(
        _mocks=_FakeMocks({"llm": "byo-key", "redis": "disabled", "mcp": "mock"}, calls),
        _config=SimpleNamespace(mocks=SimpleNamespace(modes={})),
    )
    _ExecutionContext.set_ext(ext)
    return ext


# --- outside a run -----------------------------------------------------------


def test_mode_outside_a_run_resolves_from_the_configuration(cleared_context):
    assert local.mode("mcp") == "mock"


def test_set_mode_before_a_run_is_what_the_next_resolution_sees(cleared_context):
    local.set_mode("secrets", "disabled")
    assert local.mode("secrets") == "disabled"


def test_mode_rejects_an_unknown_service(cleared_context):
    with pytest.raises(ValueError, match="Unknown service 'redsi'"):
        local.mode("redsi")


def test_set_mode_rejects_local_for_a_service_without_a_backend(cleared_context):
    with pytest.raises(ValueError, match="no local backend"):
        local.set_mode("mcp", "local")
    assert local.mode("mcp") == "mock"


def test_trace_outside_a_run_is_the_last_published_run(cleared_context, monkeypatch):
    published = [MockCall("files", "read", "local")]
    monkeypatch.setattr(trace_mod, "_LAST_RUN_CALLS", published)
    page = local.trace()
    assert (page.data, page.total) == (published, 1)


def test_an_ext_without_mocks_is_not_treated_as_a_local_run(stub_ext, monkeypatch):
    """The cloud manager is on the stack too; it serves neither modes nor a trace."""
    monkeypatch.setattr(trace_mod, "_LAST_RUN_CALLS", [])
    local.set_mode("secrets", "disabled")
    assert local.mode("secrets") == "disabled"
    assert local.trace().total == 0


# --- inside a run ------------------------------------------------------------


def test_mode_inside_a_run_is_the_live_answer_in_declared_vocabulary(local_ext):
    assert local.mode("llm") == "local"
    assert local.mode("redis") == "disabled"


def test_set_mode_inside_a_run_rejects_without_mutating_the_active_run(local_ext):
    before_modes = dict(local_ext._config.mocks.modes)
    before_llm = local.mode("llm")

    with pytest.raises(ContextError, match="before starting the next run"):
        local.set_mode("llm", "mock")

    assert local_ext._config.mocks.modes == before_modes
    assert local.mode("llm") == before_llm
    assert local_ext._mocks.synced == 0


def test_rejected_active_run_mode_change_does_not_create_a_future_override(local_ext):
    with pytest.raises(ContextError):
        local.set_mode("secrets", "disabled")

    later = SimpleNamespace(mocks=SimpleNamespace(modes={}))
    _modes.apply_overrides(later)
    assert later.mocks.modes == {}


def test_set_mode_validates_arguments_before_active_run_refusal(local_ext):
    with pytest.raises(ValueError, match="Unknown service"):
        local.set_mode("redsi", "local")
    with pytest.raises(ValueError, match="no local backend"):
        local.set_mode("mcp", "local")

    assert local_ext._config.mocks.modes == {}
    assert _modes._OVERRIDES == {}


def test_trace_inside_a_run_is_that_runs_calls(local_ext):
    page = local.trace()
    assert page.data == local_ext._mocks.trace.calls
    assert page.total == 2


# --- the mode vocabulary -----------------------------------------------------


@pytest.mark.parametrize(
    ("internal", "declared"),
    [("mock", "mock"), ("disabled", "disabled"), ("byo-key", "local"), ("local", "local")],
)
def test_to_declared_collapses_real_backends_to_local(internal, declared):
    assert _modes.to_declared(internal) == declared


@pytest.mark.parametrize(
    ("service", "declared", "internal"),
    [("llm", "local", "byo-key"), ("llm", "mock", "mock"), ("redis", "local", "local")],
)
def test_to_internal_spells_the_llm_backend_as_byo_key(service, declared, internal):
    assert _modes.to_internal(service, declared) == internal


def test_to_internal_names_an_unknown_mode_as_unknown():
    with pytest.raises(ValueError, match=r"Unknown mode 'bogus'\. Valid modes: mock, disabled"):
        _modes.to_internal("mcp", "bogus")


def test_apply_overrides_leaves_a_config_without_mocks_and_an_empty_store_alone():
    _modes.apply_overrides(SimpleNamespace())
    untouched = SimpleNamespace(mocks=SimpleNamespace(modes={"redis": "mock"}))
    _modes.apply_overrides(untouched)
    assert untouched.mocks.modes == {"redis": "mock"}
    _modes.set_override("redis", "disabled")
    _modes.apply_overrides(untouched)
    assert untouched.mocks.modes == {"redis": "disabled"}
    _modes.clear_overrides()
    assert _modes._OVERRIDES == {}


# --- conversation ------------------------------------------------------------


def _message(metadata=None) -> ConversationMessage:
    return ConversationMessage(
        role="agent",
        parts=[
            ContentPart("text/plain", "first", name="summary"),
            ContentPart("application/json", {"count": 3}, name="metrics"),
            ContentPart("text/markdown", "second"),
            ContentPart("text/plain", {"not": "a string"}, name="odd"),
            ContentPart("application/json", [1], name="summary"),
        ],
        metadata=metadata,
    )


def test_text_joins_only_string_text_parts():
    assert _message().text == "first\nsecond"


def test_get_part_is_the_first_named_match_and_none_when_absent():
    assert _message().get_part("summary").content == "first"
    assert _message().get_part("missing") is None


def test_get_parts_filters_by_exact_content_type():
    assert [p.content for p in _message().get_parts("application/json")] == [{"count": 3}, [1]]


def test_to_dict_omits_an_unnamed_parts_name_and_empty_metadata():
    data = _message().to_dict()
    assert data["parts"][2] == {"content_type": "text/markdown", "content": "second"}
    assert data["parts"][0]["name"] == "summary"
    assert "metadata" not in data
    assert _message({"model": "m"}).to_dict()["metadata"] == {"model": "m"}


def test_from_dict_round_trips_and_defaults_role_and_parts():
    original = _message({"input_tokens": 5})
    assert ConversationMessage.from_dict(original.to_dict()) == original
    assert ConversationMessage.from_dict({}) == ConversationMessage(
        role="agent", parts=[], metadata=None
    )


# --- training ----------------------------------------------------------------


@pytest.mark.parametrize("result_type", [IterationResult, EvaluationResult])
@pytest.mark.parametrize(
    ("value", "message"),
    [(math.nan, "must be finite, got nan"), (-math.inf, "must be finite, got -inf")],
)
def test_metrics_reject_non_finite_values(result_type, value, message):
    with pytest.raises(ValidationError, match=message):
        result_type(metrics={"ok": 1.0, "loss": value})


@pytest.mark.parametrize("result_type", [IterationResult, EvaluationResult])
def test_finite_metrics_are_kept_as_given(result_type):
    assert result_type(metrics={"loss": 0.25, "steps": 3}).metrics == {"loss": 0.25, "steps": 3}


def test_a_training_plan_needs_at_least_one_iteration():
    with pytest.raises(ValidationError):
        TrainingPlan(total_iterations=0)
    plan = TrainingPlan(total_iterations=1)
    assert (plan.total_iterations, plan.iteration_name) == (1, "epoch")
