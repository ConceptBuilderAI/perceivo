"""Shared isolation for the ``perceivo.managed_service`` plane tests.

Two test modules assert against the plane -- ``test_managed_service_plane.py``
for how a call resolves (mock / real / raise) and
``test_managed_service_surface_and_database.py`` for what the plane contains --
and both need the same two things: the handle to assert *through*, and a
configuration that belongs to the test rather than to the developer's machine.

``clean_config`` is registered in ``tests/unit/conftest.py`` rather than
imported by name into either module: importing a fixture shadows the parameter
of every test that requests it, which ruff reports as F811 and which is
genuinely ambiguous to read. ``_AI`` is a plain module constant, so it is
imported directly.
"""

from __future__ import annotations

import pytest

import perceivo
from perceivo.config import _ENV_OVERRIDES

_AI = perceivo.managed_service.ai


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    """Run with a known-empty configuration, not the developer's.

    ``config.load`` reads ``perceivo.yaml`` from the *current directory* and
    layers ``PERCEIVO_*`` environment variables over it, so "unconfigured" is a
    property of the machine unless both are pinned. Ambient provider keys are
    cleared too: a suite that passes because the developer happens to export
    ``OPENAI_API_KEY`` is measuring the developer.

    Returns a writer that materialises a ``perceivo.yaml`` — config is entered
    the way a user enters it, through the real loader, rather than by
    monkeypatching the loader out.
    """
    monkeypatch.chdir(tmp_path)
    for env_key in _ENV_OVERRIDES:
        monkeypatch.delenv(env_key, raising=False)
    for env_key in ("PERCEIVO_CONFIG", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "api_key"):
        monkeypatch.delenv(env_key, raising=False)

    def write(yaml_text: str) -> None:
        (tmp_path / "perceivo.yaml").write_text(yaml_text, encoding="utf-8")

    return write
