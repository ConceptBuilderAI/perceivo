"""Shared fixtures for the ``test_cov_validator_orch_*`` suites.

``_type_fn`` builds the ``node_type`` callable the port and structure checks
take; ``_write`` drops a file into a project directory. Both are used by more
than one of the split modules, so they live here rather than being copied.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _type_fn(mapping: dict[str, str]):
    """Return a node_type(node_id) callable backed by ``mapping``."""

    def _node_type(node_id: str) -> str:
        return mapping.get(node_id, "")

    return _node_type


def _write(path: Path, name: str, text: str) -> None:
    (path / name).write_text(text, encoding="utf-8")
