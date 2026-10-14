"""Filesystem path sandboxing for the local storage adapters.

The local file adapters build filesystem paths from orchestration- and
seed-supplied keys (for example ``documents/policy.txt``). Those keys are
untrusted: a key such as ``../../etc/passwd`` or ``/etc/passwd`` must never let
a read or write escape the adapter's storage root.

The primitive lives in :mod:`perceivo._internal.paths`; this module is the
adapters' import site.
"""

from __future__ import annotations

from ..._internal.paths import PathTraversalError, safe_path

__all__ = ["PathTraversalError", "safe_path"]
