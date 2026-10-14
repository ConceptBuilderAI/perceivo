"""Load a trained-model bundle onto an inference component, as the platform does.

On the platform the inference service downloads the bundle the training run
wrote -- a directory holding ``_manifest.json`` and one file per declared
artifact -- and sets each artifact on the component before the first
``predict``. Without that step ``self.model`` does not exist, so the local
drive must do the same thing over a local directory.

The manifest layout::

    {
      "artifacts": {
        "<name>": {"filename": "...", "artifact_type": "json|pickle|joblib|numpy|binary|
                   pytorch_state_dict|pytorch_module", "entry_point": true?,
                   "attribute": "<attr>"?}
      },
      "metadata": {"version": "..."}
    }

Every artifact without ``entry_point`` is set as ``component.<attribute>``
(``attribute`` defaults to the artifact name). The single ``entry_point``
artifact becomes ``component.model``; a ``pytorch_state_dict`` entry point is
rebuilt through ``component.create_model(component.model_config)``. The model
version is ``metadata.version``, falling back to ``"1.0"``.
"""

from __future__ import annotations

import io
import json
import pickle
from pathlib import Path
from typing import Any

from ..types.errors import ConfigurationError

MANIFEST_NAME = "_manifest.json"

#: The request-data key naming the local bundle directory (or a file inside it).
MODEL_PATH_KEY = "model_path"


class ModelBundleError(ConfigurationError, RuntimeError):
    """The inference component's model bundle is missing or cannot be loaded."""


def missing_bundle_message(
    component_name: str, attribute: str = "model", model_path: str | Path | None = None
) -> str:
    """Name the artifact attribute *component_name* read that no bundle set.

    With no *model_path* the message says how to supply the bundle; with one, it
    says the bundle there declares no artifact for *attribute*.
    """
    if model_path:
        return (
            f"Inference component {component_name!r} reads `self.{attribute}`, but the "
            f"model bundle at {model_path} sets no artifact with that attribute. "
            f"Add it to the bundle's {MANIFEST_NAME} (an artifact named {attribute!r}, "
            f'or one whose "attribute" is {attribute!r}), as the training run declares it. '
            f"If `self.{attribute}` is not meant to come from the bundle, check its "
            "spelling and that your code sets it before predict."
        )
    return (
        f"Inference component {component_name!r} reads `self.{attribute}`, which is set "
        f"from the trained-model bundle: the platform loads the model and every declared "
        f"artifact onto the component before calling predict, and no bundle was "
        f"supplied. Pass the bundle directory under the {MODEL_PATH_KEY!r} key of the "
        f'input (for example {{"{MODEL_PATH_KEY}": "./model", "features": {{...}}}}). The '
        f"directory must hold {MANIFEST_NAME} listing each artifact's filename and "
        "artifact_type, with exactly one marked entry_point -- the layout a training run "
        f"writes. If `self.{attribute}` is not meant to come from the bundle, check its "
        "spelling and that your code sets it before predict."
    )


def bundle_dir(model_path: str | Path) -> Path:
    """The bundle directory for *model_path*, which may name a file inside it."""
    path = Path(model_path)
    if path.is_file() or (not path.exists() and "." in path.name):
        return path.parent
    return path


def read_manifest(directory: Path) -> dict[str, Any]:
    manifest_file = directory / MANIFEST_NAME
    if not manifest_file.is_file():
        raise ModelBundleError(
            f"No artifact manifest found at {manifest_file}. A model bundle directory "
            f"must contain {MANIFEST_NAME}."
        )
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelBundleError(f"Cannot read {manifest_file}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ModelBundleError(f"{manifest_file} must hold a JSON object.")
    return manifest


def deserialize_artifact(data: bytes, artifact_type: str) -> Any:
    """Deserialize artifact bytes the way the platform loader does."""
    if artifact_type == "json":
        return json.loads(data.decode("utf-8"))
    if artifact_type == "pickle":
        return pickle.loads(data)  # the caller's own trained artifact
    if artifact_type in ("pytorch_state_dict", "pytorch_module"):
        import torch  # type: ignore[import-not-found]

        return torch.load(io.BytesIO(data), weights_only=(artifact_type == "pytorch_state_dict"))
    if artifact_type == "numpy":
        import numpy as np  # type: ignore[import-not-found,unused-ignore]

        return np.load(io.BytesIO(data), allow_pickle=False)
    if artifact_type == "joblib":
        import joblib  # type: ignore[import-not-found]

        return joblib.load(io.BytesIO(data))
    if artifact_type == "binary":
        return data
    return pickle.loads(data)  # the platform default for an unknown type


def _read_artifact(directory: Path, name: str, info: Any) -> tuple[dict[str, Any], bytes]:
    if not isinstance(info, dict) or not info.get("filename") or not info.get("artifact_type"):
        raise ModelBundleError(
            f"Artifact {name!r} in {MANIFEST_NAME} must declare filename and artifact_type."
        )
    artifact_file = directory / str(info["filename"])
    try:
        return info, artifact_file.read_bytes()
    except OSError as exc:
        raise ModelBundleError(f"Cannot read artifact {name!r} at {artifact_file}: {exc}") from exc


def _deserialize(name: str, data: bytes, artifact_type: str) -> Any:
    try:
        return deserialize_artifact(data, artifact_type)
    except ImportError as exc:
        raise ModelBundleError(
            f"Artifact {name!r} ({artifact_type}) needs a package that is not installed "
            f"locally: {exc}"
        ) from exc


def load_model_bundle(component: Any, model_path: str | Path) -> None:
    """Set every artifact in the bundle at *model_path* on *component*."""
    directory = bundle_dir(model_path)
    manifest = read_manifest(directory)
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict) or not artifacts:
        raise ModelBundleError(
            "Artifact manifest has no artifacts. "
            "The training component must declare artifacts in its manifest."
        )

    entry_point: tuple[str, dict[str, Any], bytes] | None = None
    for name, raw_info in artifacts.items():
        info, data = _read_artifact(directory, name, raw_info)
        if info.get("entry_point"):
            entry_point = (name, info, data)
            continue
        # The platform reads info.get("attribute", name), so an explicit null or empty
        # attribute fails there; refuse it here rather than run what cannot deploy.
        attribute = info.get("attribute", name)
        if not isinstance(attribute, str) or not attribute:
            raise ModelBundleError(
                f"Artifact {name!r} in {MANIFEST_NAME} has attribute {attribute!r}; "
                "give it a non-empty name, or omit the key to use the artifact name."
            )
        setattr(component, attribute, _deserialize(name, data, info["artifact_type"]))

    if entry_point is None:
        raise ModelBundleError("No entry_point artifact declared in manifest")

    name, info, data = entry_point
    artifact_type = info["artifact_type"]
    if artifact_type == "pytorch_state_dict":
        state_dict = _deserialize(name, data, artifact_type)
        model = component.create_model(getattr(component, "model_config", {}))
        model.load_state_dict(state_dict)
        model.eval()
    elif artifact_type == "pytorch_module":
        model = _deserialize(name, data, artifact_type)
        model.eval()
    else:
        model = _deserialize(name, data, artifact_type)
    component.model = model

    metadata = manifest.get("metadata") or {}
    component._model_version = (
        metadata.get("version") if isinstance(metadata, dict) else None
    ) or "1.0"
