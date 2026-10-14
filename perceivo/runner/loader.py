"""Import a component's Python module and find its ENTRY_CLASS."""

from __future__ import annotations

import importlib.util
import itertools
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("perceivo.runner.loader")

# Monotonic counter giving each load a unique module name; object ids are
# recycled and could collide two loads under one sys.modules key.
_load_counter = itertools.count()


def load_component_class(script_path: str, *, accept_class: bool = False) -> Any:
    """Load a component module and return an instance of ENTRY_CLASS.

    The module is dynamically imported from the given file path.
    The component class is found via the ``ENTRY_CLASS`` module-level
    variable (set explicitly or injected by a ``@perceivo.*`` decorator).

    Args:
        script_path: Path to the component Python file.
        accept_class: Also accept ENTRY_CLASS bound to the class itself, for the
            component types whose platform resolver takes either form.

    Returns:
        An instance of the component class.

    Raises:
        FileNotFoundError: If the script file doesn't exist.
        RuntimeError: If ENTRY_CLASS is not found or can't be instantiated.
    """
    path = Path(script_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Component script not found: {path}")

    module_name = f"perceivo_component_{path.stem}_{next(_load_counter)}"

    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        raise RuntimeError(f"Error loading component {path.name}: {exc}") from exc

    entry_class = getattr(module, "ENTRY_CLASS", None)
    if entry_class is None:
        del sys.modules[module_name]
        raise RuntimeError(
            f"No ENTRY_CLASS found in {path.name}. "
            f"Use a @perceivo decorator or define ENTRY_CLASS = 'ClassName'."
        )

    # The orchestration sandbox resolves ENTRY_CLASS by name, so it must be a
    # string; the types whose platform resolver also takes the class itself may
    # bind it, and are normalised to its name.
    if accept_class and isinstance(entry_class, type):
        entry_class = entry_class.__name__
    if not isinstance(entry_class, str):
        del sys.modules[module_name]
        got = getattr(entry_class, "__name__", type(entry_class).__name__)
        raise RuntimeError(
            f"ENTRY_CLASS in {path.name} must be the class name as a string, "
            f"not {type(entry_class).__name__}. Write ENTRY_CLASS = '{got}'. "
            f"The orchestration runtime resolves ENTRY_CLASS by name and rejects "
            f"any other form."
        )
    cls = getattr(module, entry_class, None)
    if cls is None:
        del sys.modules[module_name]
        raise RuntimeError(
            f"ENTRY_CLASS = '{entry_class}' but class '{entry_class}' "
            f"is not defined in {path.name}."
        )

    try:
        instance = cls()
    except Exception as exc:
        raise RuntimeError(f"Failed to instantiate {cls.__name__} from {path.name}: {exc}") from exc

    logger.info("Loaded component: %s from %s", cls.__name__, path.name)
    return instance


def load_manifest(manifest_path: str) -> dict[str, Any]:
    """Load a YAML manifest file.

    Args:
        manifest_path: Path to the manifest YAML file.

    Returns:
        Parsed manifest dict.
    """
    import yaml

    path = Path(manifest_path)
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    if manifest is None:
        return {}
    if not isinstance(manifest, dict):
        raise ValueError(f"Component manifest must be a YAML mapping: {path}")
    return manifest
