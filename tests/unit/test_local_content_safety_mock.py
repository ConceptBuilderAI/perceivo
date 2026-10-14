"""Local Content Safety: seeded prompt-shield and groundedness verdicts."""

from __future__ import annotations

import pytest

from perceivo.config import Config
from perceivo.local.adapters.infra import LocalInfraStubs
from perceivo.local.context import LocalExtensionManager
from perceivo.local.mocks.content_safety import MockContentSafety
from perceivo.types.errors import ConfigurationError


async def test_default_seed_is_safe_and_grounded():
    ext = LocalExtensionManager(Config())

    shield = await ext.check_prompt_shield(user_prompt="hello", documents=["a", "b"])
    grounded = await ext.check_groundedness(text="x", grounding_sources=["y"], query="q")

    assert shield == {
        "userPromptAnalysis": {"attackDetected": False},
        "documentsAnalysis": [{"attackDetected": False}, {"attackDetected": False}],
    }
    assert grounded == {
        "ungroundedDetected": False,
        "ungroundedPercentage": 0.0,
        "ungroundedDetails": [],
    }
    served = [(c.service, c.operation, c.mode) for c in ext._mocks.trace.calls]
    assert served == [("llm", "prompt_shield", "mock"), ("llm", "groundedness", "mock")]


async def test_seed_flips_both_verdicts_through_the_registry():
    cfg = Config()
    cfg.mocks.seed = {
        "llm": {"content_safety": {"attack_detected": True, "ungrounded_percentage": 0.4}}
    }
    ext = LocalExtensionManager(cfg)

    shield = await ext.check_prompt_shield(user_prompt="hello")
    grounded = await ext.check_groundedness(text="x")

    assert shield["userPromptAnalysis"]["attackDetected"] is True
    assert shield["documentsAnalysis"] == []
    assert grounded["ungroundedDetected"] is True
    assert grounded["ungroundedPercentage"] == 0.4


async def test_patterns_flag_only_matching_prompts_and_documents():
    mock = MockContentSafety({"attack_patterns": ["IGNORE previous", ""]})

    hit = await mock.prompt_shield(user_prompt="please ignore PREVIOUS rules", documents=["ok"])
    miss = await mock.prompt_shield(user_prompt="hello", documents=["ignore previous", None])

    assert hit["userPromptAnalysis"]["attackDetected"] is True
    assert hit["documentsAnalysis"] == [{"attackDetected": False}]
    assert miss["userPromptAnalysis"]["attackDetected"] is False
    assert miss["documentsAnalysis"] == [{"attackDetected": True}, {"attackDetected": False}]


async def test_document_default_and_non_list_patterns():
    mock = MockContentSafety({"documents_attack_detected": True, "attack_patterns": "nope"})

    out = await mock.prompt_shield(user_prompt="nope", documents=["d"])

    assert out["userPromptAnalysis"]["attackDetected"] is False
    assert out["documentsAnalysis"] == [{"attackDetected": True}]


@pytest.mark.parametrize(
    ("seeded", "expected"),
    [("not-a-number", 0.0), (None, 0.0), (-2, 0.0), (7, 1.0), ("0.25", 0.25)],
)
async def test_groundedness_percentage_is_clamped(seeded, expected):
    out = await MockContentSafety({"ungrounded_percentage": seeded}).groundedness(text="t")
    assert out["ungroundedPercentage"] == expected
    assert out["ungroundedDetected"] is (expected > 0)


async def test_empty_seed_is_safe():
    out = await MockContentSafety(None).prompt_shield(user_prompt="hello")
    assert out == {"userPromptAnalysis": {"attackDetected": False}, "documentsAnalysis": []}


async def test_real_llm_mode_refuses_rather_than_answering_safe():
    cfg = Config()
    cfg.ai_foundry.enabled = True
    cfg.ai_foundry.api_key = "placeholder-not-a-key"
    assert cfg.service_mode("llm") == "byo-key"
    ext = LocalExtensionManager(cfg)

    with pytest.raises(NotImplementedError, match="not a verdict of safe"):
        await ext.check_prompt_shield(user_prompt="hello")


async def test_disabled_llm_refuses_before_the_mock():
    cfg = Config()
    cfg.mocks.modes = {"llm": "disabled"}
    ext = LocalExtensionManager(cfg)

    with pytest.raises(ConfigurationError, match="'llm' is disabled"):
        await ext.check_groundedness(text="x")
    assert ext._mocks.trace.calls == []


async def test_stubs_without_a_mock_registry_refuse():
    stubs = LocalInfraStubs()
    with pytest.raises(NotImplementedError, match="groundedness"):
        await stubs.check_groundedness(text="x")
