"""Manage components programmatically.

Create, validate, inspect, and move components between machines from Python.

    from perceivo.component import create, validate, details, save_bundle, load_bundle

    # Create a new agent component
    create("my_agent", component_type="AGENT", directory="./project")

    # Validate a component
    report = validate("agent.py", "agent.yaml")

    # Get component details
    info = details("agent.py", "agent.yaml")

    # Save to the platform bundle format
    save_bundle("agent.py", "agent.yaml", output="agent_bundle.json")

    # Load one back, from disk or from a bundle already in hand
    load_bundle("agent_bundle.json", "./restored/")
"""

from . import registry
from ._manager import create, details, list_components, load_bundle, save_bundle, validate

__all__ = [
    "create",
    "details",
    "list_components",
    "load_bundle",
    "registry",
    "save_bundle",
    "validate",
]
