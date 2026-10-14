"""Build, validate, run, and publish orchestrations from Python code.

    import perceivo.orchestration as orchestration

    # Create an orchestration project on disk, then load it to edit the graph
    path = orchestration.create("my_pipeline")
    orch = orchestration.load(path)

    # Validate and run
    report = orchestration.validate(path)
    result = await orchestration.run(path, input_data={"query": "test"})

    # Publish to the platform
    published = await orchestration.publish(path)

    # Move a project between machines, in the platform bundle format
    bundle = orchestration.save_bundle(path, output="pipeline.json")
    restored = orchestration.load_bundle("pipeline.json", "./restored/")

A prebuilt orchestration is scaffolded from the template catalog instead:
``perceivo.templates.use("rag-ingest", into="./work")``.
"""

from ._builder import (
    Edge,
    Node,
    Orchestration,
    create,
    list_orchestrations,
    load,
    load_bundle,
    run,
    save_bundle,
    validate,
)
from .publish import publish

__all__ = [
    "Edge",
    "Node",
    "Orchestration",
    "create",
    "list_orchestrations",
    "load",
    "load_bundle",
    "publish",
    "run",
    "save_bundle",
    "validate",
]
