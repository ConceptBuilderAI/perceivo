"""Orchestration project scaffolding and template definitions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._models import _COMPONENT_PORT_DEFAULTS, Node
from ._orchestration import _OUT_PORT_TYPES, Orchestration
from ._prebuilt import _write_prebuilt

logger = logging.getLogger("perceivo.orchestration")


def create(
    name: str,
    directory: str = ".",
    *,
    description: str = "",
) -> Path:
    """Scaffold a new orchestration project: a minimal START → AGENT → END.

    For a richer prebuilt structure, scaffold from the template catalog
    instead: ``perceivo.templates.use("rag-ingest", into=directory)``.

    Args:
        name: Orchestration name.
        directory: Parent directory (project dir created as subdirectory).
        description: Optional description.

    Returns:
        Path to the project directory.
    """
    path = Path(directory).resolve() / name
    path.mkdir(parents=True, exist_ok=True)

    # Default: minimal START → AGENT → END
    orch = Orchestration(name, description=description or "A simple orchestration")

    start = orch.add_node("start", "START", script="start.py", manifest="start.yaml")
    agent = orch.add_node("agent", "AGENT", script="agent.py", manifest="agent.yaml")
    end = orch.add_node("end", "END", script="end.py", manifest="end.yaml")
    orch.add_edge(start, agent)
    orch.add_edge(agent, end)
    _declare_directions(orch)
    orch.save(str(path))

    from ...component import create as _create_component

    _create_component("start", "START", str(path))
    _create_component("agent", "AGENT", str(path))
    _create_component("end", "END", str(path))

    _write_project_config(path, _ENV_TEMPLATE)

    logger.info("Created orchestration '%s' at %s", name, path)
    return path


#: The two per-project configuration files a scaffold writes.
_CONFIG_FILE = "perceivo.yaml"
_ENV_FILE = ".env"


def _nearest(start: Path, filename: str) -> Path | None:
    """The nearest *filename* at or above *start*, or ``None``."""
    for candidate in (start, *start.parents):
        found = candidate / filename
        if found.is_file():
            return found
    return None


def _write_project_config(path: Path, env_text: str) -> None:
    """Write ``perceivo.yaml`` and ``.env`` into *path*, each only if none is above.

    Each file is written only when no file of that name exists at *path* or in
    any directory above it, so the tree carries exactly one of each. The walk
    looks at files, not at ``PERCEIVO_CONFIG``, so a project scaffolded under that
    variable still works when run without it.
    """
    for filename, text in ((_CONFIG_FILE, _PERCEIVO_YAML_TEMPLATE), (_ENV_FILE, env_text)):
        existing = _nearest(path, filename)
        if existing is not None:
            logger.info("Using existing %s at %s", filename, existing)
            continue
        (path / filename).write_text(text, encoding="utf-8")


def _declare_directions(orch: Orchestration) -> None:
    """Give every mainflow port the ``direction`` a platform port row carries.

    The contract check picks the port whose response_data a node sends on by
    direction; a scaffolded port without one declared no contract at all.
    """
    for node in orch.nodes.values():
        for port in node.ports:
            if port.get("port_type") in ("in", "out"):
                port.setdefault("direction", port["port_type"])


def _invoke_ports(invokes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The tool ports each INVOKE source node needs, one per distinct port name."""
    invoke_ports: dict[str, list[dict[str, Any]]] = {}
    for inv in invokes:
        node_ports = invoke_ports.setdefault(inv["source"], [])
        if not any(p["name"] == inv["port"] for p in node_ports):
            node_ports.append({"name": inv["port"], "port_type": "tool"})
    return invoke_ports


def _scaffold_from_template(
    name: str,
    path: Path,
    tpl: dict[str, Any],
    description: str,
) -> Path:
    """Build a full orchestration project from a template definition."""
    from ...component import create as _create_component

    desc = description or tpl.get("description", "")
    orch = Orchestration(name, description=desc)

    # Create each invoke source node with the named ports its invokes use;
    # add_edge raises on an undeclared port.
    chain = tpl.get("chain", [])
    invokes = tpl.get("invokes", [])
    invoke_ports = _invoke_ports(invokes)

    # An invoke-only node returns through its in port, so its out port cannot be
    # connected and is dropped. Nodes also on the mainflow keep it.
    invoke_only = {inv["target"] for inv in invokes} - set(chain)

    node_objs: dict[str, Node] = {}
    for node_def in tpl["nodes"]:
        nname = node_def["name"]
        override = None
        if nname in invoke_only:
            override = [
                dict(p)
                for p in _COMPONENT_PORT_DEFAULTS.get(node_def["type"].upper(), [])
                if p.get("port_type") not in _OUT_PORT_TYPES
            ]
        n = orch.add_node(
            nname,
            node_def["type"],
            script=f"{nname}.py",
            manifest=f"{nname}.yaml",
            extra_ports=None if override is not None else invoke_ports.get(nname),
            ports=override,
        )
        node_objs[nname] = n

    for i in range(len(chain) - 1):
        orch.add_edge(node_objs[chain[i]], node_objs[chain[i + 1]])

    for inv in invokes:
        orch.add_edge(
            node_objs[inv["source"]],
            node_objs[inv["target"]],
            source_port=inv["port"],
            kind="INVOKE",
        )
    _declare_directions(orch)
    orch.save(str(path))

    for node_def in tpl["nodes"]:
        prebuilt = node_def.get("prebuilt")
        if prebuilt:
            # Use prebuilt component (copy from prebuilt dir)
            _write_prebuilt(path, node_def["name"], prebuilt, node_def["type"])
        else:
            _create_component(node_def["name"], node_def["type"], str(path))

    # Config files. The .env lists the secrets the template needs.
    env_lines = [
        "# perceivo local development secrets",
        "# Add your API keys and secrets here.",
        "",
    ]
    env_lines += [f"# {secret}=" for secret in tpl.get("secrets", [])]
    _write_project_config(path, "\n".join(env_lines) + "\n")

    logger.info("Created orchestration '%s' from template at %s", name, path)
    return path


_PERCEIVO_YAML_TEMPLATE = """\
# perceivo.yaml — Local development configuration
version: 1

# The local-runner home defaults to .perceivo/ beside this file. A top-level
# root: (relative to this file) or PERCEIVO_HOME moves it.
# files:
#   working_dir: ./working_files  # relative to this file; default <home>/files/working
#   org_dir: ./org_files          # relative to this file; default <home>/files/org

secrets:
  source: dotenv
  dotenv_path: .env

redis:
  enabled: false
  url: redis://localhost:6379

logging:
  level: INFO
  format: console
"""

_ENV_TEMPLATE = """\
# perceivo local development secrets
# Add your API keys and secrets here.
# These are loaded by perceivo.secrets.get("key_name")

# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
"""


_ORCHESTRATION_TEMPLATES: dict[str, dict[str, Any]] = {
    # A TOOL has no phantom out-port, so it cannot sit on the mainflow; these
    # pipelines put an AGENT there and invoke the tools from it.
    "rag-ingest": {
        "description": "RAG ingestion pipeline: read PDF, split text, embed, upsert into Milvus",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "ingest", "type": "AGENT", "prebuilt": None},
            {"name": "pdf_reader", "type": "TOOL", "prebuilt": "pdf_reader"},
            {"name": "text_splitter", "type": "TOOL", "prebuilt": "text_splitter"},
            {"name": "embeddings", "type": "TOOL", "prebuilt": "openai_embeddings"},
            {"name": "milvus_upsert", "type": "TOOL", "prebuilt": "milvus_upsert"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "ingest", "end"],
        "invokes": [
            {"source": "ingest", "target": "pdf_reader", "port": "invoke_pdf_reader"},
            {"source": "ingest", "target": "text_splitter", "port": "invoke_text_splitter"},
            {"source": "ingest", "target": "embeddings", "port": "invoke_embeddings"},
            {"source": "ingest", "target": "milvus_upsert", "port": "invoke_milvus_upsert"},
        ],
        "secrets": ["OPENAI_API_KEY"],
    },
    "rag-query": {
        "description": "RAG query pipeline: embed query, search Milvus, generate answer with LLM",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "chat", "type": "AGENT", "prebuilt": "multi_provider_chat"},
            {"name": "embeddings", "type": "TOOL", "prebuilt": "openai_embeddings"},
            {"name": "milvus_search", "type": "TOOL", "prebuilt": "milvus_search"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "chat", "end"],
        "invokes": [
            {"source": "chat", "target": "embeddings", "port": "invoke_embeddings"},
            {"source": "chat", "target": "milvus_search", "port": "invoke_milvus_search"},
        ],
        "secrets": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    },
    "chatbot-simple": {
        "description": "Simple LLM chatbot: start → multi-provider chat → end",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "chat", "type": "AGENT", "prebuilt": "multi_provider_chat"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "chat", "end"],
        "invokes": [],
        "secrets": ["ANTHROPIC_API_KEY"],
    },
    "chatbot-multi-turn": {
        "description": "Multi-turn chatbot with conversation memory",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "chat", "type": "AGENT", "prebuilt": "multi_provider_chat"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "chat", "end"],
        "invokes": [],
        "secrets": ["ANTHROPIC_API_KEY"],
    },
    "agent-tool-use": {
        "description": "Agent with tool use: LLM agent invokes HTTP and database tools",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "agent", "type": "AGENT", "prebuilt": "multi_provider_chat"},
            {"name": "http_tool", "type": "TOOL", "prebuilt": "http_request"},
            {"name": "db_tool", "type": "TOOL", "prebuilt": "database_query"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "agent", "end"],
        "invokes": [
            {"source": "agent", "target": "http_tool", "port": "invoke_http"},
            {"source": "agent", "target": "db_tool", "port": "invoke_db"},
        ],
        "secrets": ["ANTHROPIC_API_KEY"],
    },
    "image-analyzer": {
        "description": "Multimodal image analysis: describe image with vision LLM, extract tags",
        "nodes": [
            {"name": "start", "type": "START", "prebuilt": None},
            {"name": "image_describer", "type": "AGENT", "prebuilt": "anthropic_chat"},
            {"name": "tag_extractor", "type": "TOOL", "prebuilt": "multi_provider_chat"},
            {"name": "end", "type": "END", "prebuilt": None},
        ],
        "chain": ["start", "image_describer", "end"],
        "invokes": [
            {
                "source": "image_describer",
                "target": "tag_extractor",
                "port": "invoke_tag_extractor",
            },
        ],
        "secrets": ["ANTHROPIC_API_KEY"],
    },
}
