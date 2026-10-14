"""Platform export-bundle schema for components and orchestrations.

The repository's schema-pin controls keep this component and orchestration
subset aligned with the supported platform export contract. Intelligence export
is platform-only and omitted. The SDK validates every locally produced bundle
against these models before import.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from perceivo.types.errors import ConfigurationError


class NestedOrchestrationError(ConfigurationError, ValueError):
    """A nested orchestration declaration is invalid or cannot be exported.

    Subclasses ``ConfigurationError`` and ``ValueError``; pydantic wraps it when it
    is raised inside a model validator.
    """


class PortExportData(BaseModel):
    direction: str  # "IN" or "OUT"
    key: str
    label: str | None = None
    port_type: str  # "in", "out", "state", "tool", "policy", ...
    contract: dict[str, Any] | None = None
    tool_call: bool = False
    post_tool_contract: dict[str, Any] | None = None
    base_schema: dict[str, Any] | None = None
    artifact_type_constraint: str | None = None
    allowed_states: list[Any] | None = None
    request_data_schema: dict[str, Any] | None = None
    response_data_schema: dict[str, Any] | None = None
    artifact_type_input: str | None = None
    artifact_type_output: str | None = None
    artifact_input_schema: dict[str, Any] | None = None
    artifact_output_schema: dict[str, Any] | None = None
    multi_artifact: bool | None = None
    artifact_types: list[Any] | None = None
    response_data_passthrough: str | None = None
    response_data_passthrough_except: list[Any] | None = None
    response_data_contract: str | None = None


class FSMStateExportData(BaseModel):
    state_key: str
    label: str
    in_port_key: str  # references a port by KEY (not UUID)
    out_port_key: str
    is_start: bool = False
    is_end: bool = False


class ComponentExportData(BaseModel):
    asset_id: str | None = None
    name: str
    category: str  # TOOL, AGENT, FSM, START, END, ...
    description: str | None = None
    short_description: str | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None
    additional_packages: list[str] | None = None
    requires_gpu: bool = False
    artifact_type_input: str | None = None
    artifact_type_output: str | None = None
    script: str | None = None
    manifest_yaml: str | None = None  # plaintext YAML in the bundle
    ports: list[PortExportData] = Field(default_factory=list)
    fsm_states: list[FSMStateExportData] = Field(default_factory=list)
    is_template_clone: bool = False
    template_source_id: str | None = None
    is_prebuilt_reference: bool = False
    prebuilt_asset_id: str | None = None


class ComponentExport(BaseModel):
    export_version: str = "1.0"
    export_type: Literal["component"] = "component"
    exported_at: datetime
    component: ComponentExportData


class OrchestrationExportMetadata(BaseModel):
    name: str
    description: str | None = None
    tags: list[Any] | None = None
    additional_packages: list[str] | None = None
    requires_gpu: bool = False
    runtime_config: dict[str, Any] | None = None
    disable_intermediate_snapshots: bool = False


class NodeExportData(BaseModel):
    local_id: str  # e.g. "node_0"
    component_ref: str  # key into the components dict
    # A nested-orchestration node carries the child's portable ref, which keys
    # into the sibling ``orchestrations`` map. None for ordinary components.
    orchestration_ref: str | None = None
    component_asset_id: str | None = None
    name: str | None = None
    position: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    orchestration_asset_id: str | None = None

    @model_validator(mode="after")
    def _check_nesting_exclusivity(self) -> Self:
        """A node embeds a child or references one; it cannot do both."""
        if self.orchestration_ref and self.orchestration_asset_id:
            raise NestedOrchestrationError(
                f"Node '{self.local_id}' sets both orchestration_ref "
                f"('{self.orchestration_ref}') and orchestration_asset_id "
                f"('{self.orchestration_asset_id}'). Embed a child OR reference an "
                f"existing one, not both."
            )
        return self


class PortRefExportData(BaseModel):
    node_ref: str  # references a node by local_id
    component_port_key: str
    component_port_direction: str  # "IN" or "OUT"
    alias: str | None = None
    effective_contract: dict[str, Any] | None = None


class EdgeExportData(BaseModel):
    source_port_index: int
    target_port_index: int
    edge_kind: str  # MAINFLOW, INVOKE, RETURN, TRANSITION


class SecretHintData(BaseModel):
    alias: str
    hint: str | None = None
    node_ref: str | None = None


class OrchestrationExport(BaseModel):
    export_version: str = "1.0"
    export_type: Literal["orchestration"] = "orchestration"
    exported_at: datetime
    orchestration: OrchestrationExportMetadata
    components: dict[str, ComponentExportData]
    nodes: list[NodeExportData]
    ports: list[PortRefExportData]
    edges: list[EdgeExportData]
    secrets_required: list[SecretHintData] = Field(default_factory=list)
    # Embedded sub-orchestrations use the same portable ref carried by the node.
    # The mapping is recursive. A flat orchestration omits the key entirely.
    orchestrations: dict[str, OrchestrationExport] | None = None


# Resolve the recursive ``orchestrations`` forward reference at import, so a
# broken annotation fails immediately.
OrchestrationExport.model_rebuild()
