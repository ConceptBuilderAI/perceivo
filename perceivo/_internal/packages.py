"""Declared-dependency resolution for ``additional_packages``.

A component manifest and an orchestration bundle both declare
``additional_packages``: the third-party distributions the platform installs into
the workload image before the orchestration runs. The SDK does not install them
locally. Instead, :func:`missing_packages` reports which declarations are not
installed in this interpreter, the import path warns with that answer, and
``perceivo run`` re-checks it against the interpreter executing the run.

Resolution reads distribution metadata (``importlib.metadata``) rather than
attempting an import: a distribution name (``pillow``, ``beautifulsoup4``) often
differs from its import name (``PIL``, ``bs4``), and importing would execute
package code as a side effect of a warning.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, distribution

# PEP 508: the distribution is the name alone, before any extras, version
# specifier, environment marker or URL.
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def requirement_name(declaration: str) -> str:
    """Return the bare distribution name in a PEP 508 requirement string, or ``""``."""
    match = _REQUIREMENT_NAME.match(str(declaration))
    return match.group(1) if match else ""


def is_installed(declaration: str) -> bool:
    """Return whether the distribution named by *declaration* is installed here.

    An unparseable declaration is reported as not installed.
    """
    name = requirement_name(declaration)
    if not name:
        return False
    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


def missing_packages(declarations: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return the declarations with no installed distribution.

    Order-preserving and de-duplicated, so the warning reads the way the project
    file does.
    """
    seen: set[str] = set()
    missing: list[str] = []
    for raw in declarations or []:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        if not is_installed(text):
            missing.append(text)
    return missing


def missing_packages_warning(missing: list[str], *, where: str) -> str:
    """Return the warning both the import path and the runner emit."""
    return (
        f"{where} declares additional_packages that are NOT installed in this "
        f"interpreter: {', '.join(missing)}. The platform installs these into the "
        f"workload image; the SDK cannot, so this local run may diverge from the "
        f"platform. Install them into the environment running `perceivo run` "
        f"(pip install {' '.join(missing)}) before trusting the result."
    )
