"""Fixtures shared across ``tests/unit``.

``clean_config`` is registered here rather than imported into each test module:
importing a fixture by name shadows the parameter of every test that requests
it, which ruff reports as F811 and which is genuinely ambiguous to read. The
public-API fixtures (``no_network``, ``clean_credentials``, ``api`` and
``project``) are registered here for the same reason.
"""

from __future__ import annotations

from .fixtures_managed_service import clean_config
from .fixtures_public_api import api, clean_credentials, no_network, project

__all__ = ["api", "clean_config", "clean_credentials", "no_network", "project"]
