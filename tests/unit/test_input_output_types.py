"""T1: Input/output dataclass shapes."""

from __future__ import annotations

import dataclasses

import pytest

from perceivo.types.artifact import Artifact, ArtifactType
from perceivo.types.inputs import (
    AdapterInput,
    AgentInput,
    EndInput,
    FsmInput,
    PolicyInput,
    ReviewInput,
    StartInput,
    ToolInput,
)
from perceivo.types.outputs import (
    AgentOutput,
    EndOutput,
    PolicyOutput,
    StartOutput,
    ToolOutput,
)


def test_artifact_type_enum_values():
    assert {t.value for t in ArtifactType} == {"none", "text", "json", "file"}


def test_artifact_coerces_string_type_to_enum():
    art = Artifact(type="text", data="hi")
    assert art.type is ArtifactType.TEXT
    assert art.data == "hi"
    assert art.mime_type == "text/plain"


def test_artifact_supports_legacy_mapping_access():
    art = Artifact(type=ArtifactType.TEXT, data="hi")
    assert art["type"] == "text"
    assert art.get("data") == "hi"
    assert art.get("missing", "fallback") == "fallback"
    with pytest.raises(KeyError):
        _ = art["missing"]


def test_artifact_rejects_unknown_string_type():
    with pytest.raises(ValueError):
        Artifact(type="bogus", data=None)


def test_artifact_optional_fields_default_to_none():
    art = Artifact(type=ArtifactType.JSON, data={"k": 1})
    assert art.file_name is None
    assert art.name is None


REQUIRES_ARTIFACT = [AgentInput, EndInput, FsmInput, PolicyInput]
ARTIFACT_OPTIONAL = [StartInput, ToolInput, ReviewInput, AdapterInput]


@pytest.mark.parametrize("cls", REQUIRES_ARTIFACT)
def test_inputs_requiring_artifact_raise_without_it(cls):
    with pytest.raises(TypeError):
        cls()


@pytest.mark.parametrize("cls", REQUIRES_ARTIFACT)
def test_inputs_requiring_artifact_accept_one(cls):
    art = Artifact(type="text", data="x")
    instance = cls(artifact=art)
    assert instance.artifact is art
    assert instance.artifacts == {}


@pytest.mark.parametrize("cls", ARTIFACT_OPTIONAL)
def test_inputs_with_optional_artifact_construct_without_args(cls):
    instance = cls()
    assert instance.artifact is None
    assert instance.request_data == {}
    assert instance.artifacts == {}


@pytest.mark.parametrize(
    "cls",
    [
        StartInput,
        AgentInput,
        ToolInput,
        FsmInput,
        PolicyInput,
        ReviewInput,
        AdapterInput,
    ],
)
def test_every_input_class_has_request_data_and_artifacts(cls):
    fields = {f.name for f in dataclasses.fields(cls)}
    assert "request_data" in fields
    assert "artifacts" in fields
    assert "artifact" in fields


def test_end_input_has_response_data_and_artifact():
    """``EndInput`` is the sole input type that carries ``response_data`` instead of ``request_data``."""
    fields = {f.name for f in dataclasses.fields(EndInput)}
    assert "response_data" in fields
    assert "artifact" in fields
    assert "artifacts" in fields


@pytest.mark.parametrize("cls", [StartOutput, ToolOutput])
def test_optional_artifact_outputs_construct_with_no_args(cls):
    instance = cls()
    assert dataclasses.is_dataclass(instance)
    assert instance.artifact is None


def test_agent_output_requires_artifact():
    """AgentOutput is the strictest output — artifact is non-optional."""
    with pytest.raises(TypeError):
        AgentOutput()
    art = Artifact(type="text", data="hi")
    out = AgentOutput(artifact=art)
    assert out.artifact is art


def test_end_output_defaults_to_success():
    out = EndOutput(response_data={"status": "success"})

    assert out.failed is False
    assert out.failure_reason is None
    assert out.response_data == {"status": "success"}


def test_end_output_can_fail_without_discarding_result():
    artifact = Artifact(type="json", data={"insert_count": 2})
    out = EndOutput(
        artifact,
        {"status": "failed", "insert_count": 2},
        {"main": artifact},
        True,
        "two chunks failed",
    )

    assert out.failed is True
    assert out.failure_reason == "two chunks failed"
    assert out.artifact is artifact
    assert out.artifacts == {"main": artifact}


def test_policy_output_has_next_state_or_equivalent():
    fields = {f.name for f in dataclasses.fields(PolicyOutput)}
    assert "next_state" in fields or "next" in fields or "transition" in fields
