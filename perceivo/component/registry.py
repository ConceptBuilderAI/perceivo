"""Install, list and use shared components from a local registry.

Components can be installed from local paths or git repositories into
a local registry at ``<home>/registry/``, where ``<home>`` is the local-runner
root (``PERCEIVO_HOME``, else the nearest ``perceivo.yaml``'s ``root:`` or
``.perceivo`` beside it, else ``./.perceivo``; see ``Config.root``). A user who wants one registry for
every project sets ``PERCEIVO_HOME``. Installed components
can then be referenced by name in orchestrations.

Example::

    import perceivo

    # Install a component
    perceivo.component.registry.install("./my-tool/")
    perceivo.component.registry.install("https://github.com/user/perceivo-tools.git", path="tools/embedder")

    # List installed
    for comp in perceivo.component.registry.list_installed():
        print(comp["name"], comp["component_type"])

    # Use in orchestration: copy the component into the project, then point
    # the node's script at the copied file
    script = perceivo.component.registry.copy_to_project("embedder", "./my-pipeline")
    orch = perceivo.orchestration.Orchestration("my-pipeline")
    orch.add_node("my_tool", "TOOL", script=str(script))
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml

from .._internal.home import session_tmp as _session_tmp
from .._internal.home import subdir as _home_subdir
from ..validator._component_type import ComponentTypeError, resolve_component_type

__all__ = [
    "copy_to_project",
    "get",
    "get_manifest",
    "get_script",
    "install",
    "list_installed",
    "uninstall",
]

logger = logging.getLogger("perceivo.component.registry")

_REGISTRY_META_FILE = "registry.json"

#: A fixed registry directory, when one is pinned (tests pin it). ``None`` means
#: ``<home>/registry``, resolved from the working directory on each call.
_REGISTRY_DIR: Optional[Path] = None


def _registry_root() -> Path:
    """The registry directory. Resolves only; creates nothing."""
    return _REGISTRY_DIR if _REGISTRY_DIR is not None else _home_subdir("registry")


def _ensure_registry() -> Path:
    """Ensure the registry directory exists."""
    root = _registry_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def install(
    source: str,
    *,
    name: Optional[str] = None,
    path: Optional[str] = None,
) -> dict[str, Any]:
    """Install a component into the local registry.

    Args:
        source: Local directory path or git repository URL.
        name: Override the component name (defaults to the manifest's
            ``name``, else the script file's stem).
        path: Subdirectory within a git repo containing the component.

    Returns:
        Dict with ``name``, ``component_type``, ``description``, ``script``,
        ``manifest``, ``source`` and ``tags``.

    Raises:
        FileNotFoundError: If the source doesn't contain a valid component.
        ValueError: If no script/manifest pair is found.
    """
    registry = _ensure_registry()
    # A git source is cloned into a session scratch directory under <home>/tmp,
    # which is removed when the install returns or raises.
    with _session_tmp("perceivo_registry_") as scratch:
        return _install_from(registry, _resolve_source(source, path, scratch), source, name)


def _install_from(
    registry: Path, source_path: Path, source: str, name: Optional[str]
) -> dict[str, Any]:
    """Copy the component found at ``source_path`` into ``registry``."""
    script_file, manifest_file = _find_component_files(source_path)
    if not script_file or not manifest_file:
        raise ValueError(
            f"No valid component found at {source_path}. "
            "Expected a .py script with ENTRY_CLASS and a .yaml manifest."
        )

    manifest_text = manifest_file.read_text(encoding="utf-8")
    manifest_data = yaml.safe_load(manifest_text) or {}
    comp_name = name or manifest_data.get("name") or script_file.stem
    comp_name = _safe_name(comp_name)

    dest = registry / comp_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copy2(script_file, dest / script_file.name)
    shutil.copy2(manifest_file, dest / manifest_file.name)

    # The type validate reports, so a category-only manifest is not recorded as a tool.
    try:
        component_type = resolve_component_type(str(script_file), None, manifest_text)
    except ComponentTypeError:
        component_type = "tool"

    meta = {
        "name": comp_name,
        "component_type": component_type,
        "description": manifest_data.get("description", ""),
        "script": script_file.name,
        "manifest": manifest_file.name,
        "source": source,
        "tags": manifest_data.get("tags", []),
    }
    (dest / _REGISTRY_META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    logger.info("Installed component '%s' to registry", comp_name)
    return meta


def uninstall(name: str) -> bool:
    """Remove a component from the local registry.

    Args:
        name: Component name.

    Returns:
        True if removed, False if not found.
    """
    dest = _registry_root() / _safe_name(name)
    if dest.exists():
        shutil.rmtree(dest)
        logger.info("Uninstalled component '%s'", name)
        return True
    return False


def list_installed() -> list[dict[str, Any]]:
    """List all components in the local registry.

    Returns:
        List of dicts with name, component_type, description, path.
    """
    registry = _ensure_registry()
    results = []

    for comp_dir in sorted(registry.iterdir()):
        meta_file = comp_dir / _REGISTRY_META_FILE
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["path"] = str(comp_dir)
            results.append(meta)
        except Exception:
            continue

    return results


def get(name: str) -> Optional[dict[str, Any]]:
    """Get info about an installed component.

    Args:
        name: Component name.

    Returns:
        Component metadata dict, or None if not found.
    """
    dest = _registry_root() / _safe_name(name)
    meta_file = dest / _REGISTRY_META_FILE
    if not meta_file.exists():
        return None
    meta: dict[str, Any] = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["path"] = str(dest)
    return meta


def get_script(name: str) -> Optional[str]:
    """Get the script content for an installed component.

    Args:
        name: Component name.

    Returns:
        Script content as string, or None if not found.
    """
    info = get(name)
    if not info:
        return None
    script_file: Path = Path(info["path"]) / info["script"]
    if script_file.exists():
        return script_file.read_text(encoding="utf-8")
    return None


def get_manifest(name: str) -> Optional[str]:
    """Get the manifest content for an installed component.

    Args:
        name: Component name.

    Returns:
        Manifest YAML content as string, or None if not found.
    """
    info = get(name)
    if not info:
        return None
    manifest_file: Path = Path(info["path"]) / info["manifest"]
    if manifest_file.exists():
        return manifest_file.read_text(encoding="utf-8")
    return None


def copy_to_project(name: str, project_dir: str, *, file_name: Optional[str] = None) -> Path:
    """Copy a registry component into a project directory.

    Args:
        name: Component name in the registry.
        project_dir: Target project directory.
        file_name: Override the file name (without extension).

    Returns:
        Path to the copied script file.

    Raises:
        ValueError: If component not found in registry.
    """
    info = get(name)
    if not info:
        raise ValueError(f"Component '{name}' not found in registry.")

    src_dir = Path(info["path"])
    dest_dir = Path(project_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    fname = file_name or name

    script_src = src_dir / info["script"]
    manifest_src = src_dir / info["manifest"]

    script_dest = dest_dir / f"{fname}.py"
    manifest_dest = dest_dir / f"{fname}.yaml"

    shutil.copy2(script_src, script_dest)
    shutil.copy2(manifest_src, manifest_dest)

    logger.info("Copied component '%s' to %s", name, dest_dir)
    return script_dest


def _resolve_source(source: str, sub_path: Optional[str], scratch: Path) -> Path:
    """Resolve source to a local directory path; a git source is cloned into ``scratch``."""
    # Derive the scheme without embedding a clear-text URL literal. Insecure
    # cleartext transports are refused so registry clones stay over TLS.
    scheme = source.split("://", 1)[0].lower() if "://" in source else ""
    if scheme in {"http", "ftp", "telnet"}:
        raise ValueError(
            f"Insecure '{scheme}://' registry sources are not allowed; use 'https://'."
        )
    if scheme == "https" or source.endswith(".git"):
        tmpdir = str(scratch)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source, tmpdir],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to clone {source}: {e.stderr.decode()}")
        result = Path(tmpdir)
        if sub_path:
            result = result / sub_path
    else:
        result = Path(source).resolve()
        if sub_path:
            result = result / sub_path

    if not result.exists():
        raise FileNotFoundError(f"Source path does not exist: {result}")

    return result


def _find_script_file(py_files: list[Path]) -> Optional[Path]:
    """Return the first .py with ENTRY_CLASS, else the first .py (if any)."""
    for py in py_files:
        if "ENTRY_CLASS" in py.read_text(encoding="utf-8"):
            return py
    if py_files:
        return py_files[0]
    return None


def _first_typed_manifest(yaml_files: list[Path]) -> Optional[Path]:
    """Return the first manifest declaring an ``extension_type``."""
    for yf in yaml_files:
        data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        if "extension_type" in data:
            return yf
    return None


def _find_manifest_file(script_file: Optional[Path], yaml_files: list[Path]) -> Optional[Path]:
    """Find the manifest paired with ``script_file`` (falling back sensibly)."""
    if script_file:
        expected_yaml = script_file.with_suffix(".yaml")
        if expected_yaml in yaml_files:
            return expected_yaml
        typed = _first_typed_manifest(yaml_files)
        if typed is not None:
            return typed
    if yaml_files:
        return yaml_files[0]
    return None


def _find_component_files(directory: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Find a script + manifest pair in a directory."""
    py_files = list(directory.glob("*.py"))
    yaml_files = list(directory.glob("*.yaml"))

    script_file = _find_script_file(py_files)
    manifest_file = _find_manifest_file(script_file, yaml_files)

    return script_file, manifest_file


def _safe_name(name: str) -> str:
    """Sanitize a component name for filesystem use."""
    import re

    name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(name).lower())
    return re.sub(r"_+", "_", name).strip("_") or "component"
