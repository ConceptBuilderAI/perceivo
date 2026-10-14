"""An inference component loads its trained-model bundle locally as the platform does.

The runner reads the bundle's ``_manifest.json``, loads the entry-point artifact
into ``self.model`` and each named artifact into its attribute, and names any
artifact the component reads but the bundle does not supply.
"""

from __future__ import annotations

import json
import pickle
import textwrap
from pathlib import Path

import pytest

from perceivo.runner import run_component
from perceivo.types.errors import ConfigurationError, PerceivoError

# --- inference: the model bundle -------------------------------------------------

INFERENCE = """
from perceivo.intelligence import InferenceComponent

class Infer(InferenceComponent):
    async def predict(self, input):
        label = self.model["labels"][input.features["x"]]
        return {"label": label, "names": self.feature_names, "v": self._model_version}

ENTRY_CLASS = "Infer"
"""


def _script(tmp_path: Path) -> str:
    path = tmp_path / "infer.py"
    path.write_text(textwrap.dedent(INFERENCE), encoding="utf-8")
    return str(path)


def _bundle(tmp_path: Path, *, entry: bool = True) -> Path:
    bundle = tmp_path / "model"
    bundle.mkdir()
    (bundle / "model.pkl").write_bytes(pickle.dumps({"labels": {"1": "one", "2": "two"}}))
    (bundle / "features.json").write_text(json.dumps(["x"]), encoding="utf-8")
    manifest = {
        "artifacts": {
            "classifier": {
                "filename": "model.pkl",
                "artifact_type": "pickle",
                "entry_point": entry,
            },
            "names": {
                "filename": "features.json",
                "artifact_type": "json",
                "attribute": "feature_names",
            },
        },
        "metadata": {"version": "7"},
    }
    (bundle / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


async def test_inference_loads_the_bundle_before_predict(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    result = await run_component(
        _script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle), "features": {"x": "2"}},
    )
    assert result.success, result.error
    assert result.response_data["result"] == {"label": "two", "names": ["x"], "v": "7"}


async def test_inference_accepts_a_file_inside_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    result = await run_component(
        _script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle / "model.pkl"), "features": {"x": "1"}},
    )
    assert result.success, result.error
    assert result.response_data["result"]["label"] == "one"


async def test_inference_without_a_bundle_says_how_to_supply_one(tmp_path: Path) -> None:
    result = await run_component(
        _script(tmp_path), component_type="INFERENCE", input_data={"features": {"x": "1"}}
    )
    assert not result.success
    assert "model_path" in str(result.error)
    assert "_manifest.json" in str(result.error)
    assert "has no attribute" not in str(result.error)


async def test_inference_bundle_without_an_entry_point_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, entry=False)
    result = await run_component(
        _script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle), "features": {"x": "1"}},
    )
    assert not result.success
    assert "entry_point" in str(result.error)


def test_bundle_errors_are_rooted() -> None:
    from perceivo.runner._model_bundle import ModelBundleError

    assert issubclass(ModelBundleError, PerceivoError)
    assert issubclass(ModelBundleError, ConfigurationError)


# --- inference: every missing artifact is named ------------------------------------

_MF_INFERENCE = """
from perceivo.intelligence import InferenceComponent

class MF(InferenceComponent):
    async def predict(self, input):
        index = self.user_id_map.get(input.features["user"], 0)
        return {"index": index, "model": self.model["labels"]["1"]}

ENTRY_CLASS = "MF"
"""


def _mf_script(tmp_path: Path) -> str:
    path = tmp_path / "mf.py"
    path.write_text(textwrap.dedent(_MF_INFERENCE), encoding="utf-8")
    return str(path)


async def test_inference_names_a_missing_declared_artifact(tmp_path: Path) -> None:
    result = await run_component(
        _mf_script(tmp_path), component_type="INFERENCE", input_data={"features": {"user": "u"}}
    )
    assert not result.success
    assert "self.user_id_map" in str(result.error)
    assert "model_path" in str(result.error)


async def test_inference_names_an_artifact_the_bundle_lacks(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    result = await run_component(
        _mf_script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle), "features": {"user": "u"}},
    )
    assert not result.success
    assert "self.user_id_map" in str(result.error)
    assert str(bundle) in str(result.error)


async def test_inference_loads_a_bundle_artifact_by_its_attribute(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "users.json").write_text(json.dumps({"u": 4}), encoding="utf-8")
    manifest = json.loads((bundle / "_manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["user_id_map"] = {"filename": "users.json", "artifact_type": "json"}
    (bundle / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = await run_component(
        _mf_script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle), "features": {"user": "u"}},
    )
    assert result.success, result.error
    assert result.response_data["result"] == {"index": 4, "model": "one"}


@pytest.mark.parametrize("attribute", [None, ""])
async def test_inference_refuses_an_artifact_attribute_the_platform_cannot_set(
    tmp_path: Path, attribute: object
) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "users.json").write_text(json.dumps({"u": 4}), encoding="utf-8")
    manifest = json.loads((bundle / "_manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["user_id_map"] = {
        "filename": "users.json",
        "artifact_type": "json",
        "attribute": attribute,
    }
    (bundle / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = await run_component(
        _mf_script(tmp_path),
        component_type="INFERENCE",
        input_data={"model_path": str(bundle), "features": {"user": "u"}},
    )
    assert not result.success
    assert "'user_id_map'" in str(result.error)
    assert "non-empty name" in str(result.error)


def test_the_missing_artifact_message_also_points_at_a_misspelt_attribute() -> None:
    from perceivo.runner._model_bundle import missing_bundle_message

    for model_path in (None, "./model"):
        message = missing_bundle_message("MF", "modle", model_path)
        assert "self.modle" in message
        assert "check its spelling" in message
