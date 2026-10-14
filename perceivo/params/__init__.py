"""Read node configuration: the input parameters a node is configured with.

perceivo.params.get(name, default)   one value
perceivo.params.all()                every value, as a dict
perceivo.params.keys()               every configured name
perceivo.params.declaration(name)    what the manifest declared about one
"""

from __future__ import annotations

from ._variables import all, declaration, get, keys

__all__ = ["all", "declaration", "get", "keys"]
