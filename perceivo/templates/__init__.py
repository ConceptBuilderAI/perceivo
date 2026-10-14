"""perceivo.templates: the template catalog.

A template is a *provenance*, not a node type: a prebuilt master you copy into
a project. What it produces is always an ordinary component of some type, or
an ordinary orchestration project. One catalog, one name set, in kebab-case::

    perceivo.templates.list(kind="component")        # or "orchestration", or None for both
    perceivo.templates.get("llm-request")            # one entry
    perceivo.templates.use("llm-request", into="./my_project")
    perceivo.templates.use("rag-ingest", into="./work", as_name="docs_rag")

``use`` of a component template writes ``<into>/<n>/<n>.py`` and ``<n>.yaml``
(plus a ``<n>.component.json`` import record), where ``<n>`` is ``as_name`` or
the template name in snake case; ``use`` of an orchestration template scaffolds
a full project under ``<into>/``. Component templates copied from the
platform library carry that library asset's id as ``template_source_id``, so a
component built from one stays linked to its source when published.
"""

from ._catalog import Template, get, list, use

__all__ = ["Template", "get", "list", "use"]
