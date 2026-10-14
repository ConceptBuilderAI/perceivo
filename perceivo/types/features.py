"""
Feature types for perceivo SDK.

These are the typed pair for :class:`perceivo.intelligence.FeatureComponent` --
the schema an intelligence declares, the input its ingestion receives, the plan
it returns, the per-batch result it produces, and the sample stream training
reads back.

A feature extension implements four methods, and every one of them is typed by
something in this module::

    get_schema()      -> FeatureSchema
    prepare_data(IngestInput)  -> IngestPlan
    process_batch(int)         -> BatchResult
    get_samples(SampleRequest) -> AsyncIterator[SampleBatch]

Example:
    ```python
    from perceivo.types import FeatureSchema, PropertyDefinition, PropertyType

    schema = FeatureSchema(
        properties={
            "user_id": PropertyDefinition(name="user_id", dtype=PropertyType.STRING),
            "score": PropertyDefinition(name="score", dtype=PropertyType.FLOAT64),
            "event_timestamp": PropertyDefinition(
                name="event_timestamp", dtype=PropertyType.TIMESTAMP
            ),
        },
        entity_key=["user_id"],
        timestamp_field="event_timestamp",
    )
    ```
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class PropertyType(str, Enum):
    """
    Feature property data types.

    These map to Feast and to the underlying online/offline store types.
    """

    INT64 = "int64"
    FLOAT64 = "float64"
    STRING = "string"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    LIST_INT64 = "list[int64]"
    LIST_FLOAT64 = "list[float64]"
    LIST_STRING = "list[string]"


class PropertyDefinition(BaseModel):
    """Definition of a single feature property (column)."""

    name: str = Field(..., description="Property name (unique within the feature view)")
    dtype: PropertyType = Field(..., description="Data type")
    description: str | None = Field(None, description="Human-readable description")
    nullable: bool = Field(True, description="Whether null values are allowed")
    default: Any | None = Field(None, description="Default value when null")


class FeatureSchema(BaseModel):
    """
    Schema defining the structure of an intelligence's features.

    Declares the properties (columns), the entity key used for lookups, and
    which property carries the event timestamp. Returned by
    ``FeatureComponent.get_schema()`` and used to configure and validate the
    feature store.

    Construction checks that the three agree, so a typo fails here rather than
    as a feature view built on the wrong column: each ``properties`` key must
    equal its definition's ``name``, and every ``entity_key`` entry and the
    ``timestamp_field`` must name a declared property. Each error names the
    declared properties.
    """

    properties: dict[str, PropertyDefinition] = Field(
        ..., description="Property name -> PropertyDefinition"
    )
    entity_key: list[str] = Field(
        ..., min_length=1, description="Property names that form the entity key"
    )
    timestamp_field: str = Field(..., description="Name of the timestamp property")
    version: str = Field("1.0", description="Schema version")
    description: str | None = Field(None, description="Schema description")

    @model_validator(mode="after")
    def _names_agree(self) -> FeatureSchema:
        problems = self._name_problems()
        if problems:
            raise PydanticCustomError(
                "feature_schema_names", "{detail}", {"detail": "; ".join(problems)}
            )
        return self

    def _name_problems(self) -> list[str]:
        """Each disagreement between ``properties``, ``entity_key`` and the timestamp."""
        problems = [
            f"properties key {key!r} holds {prop.name!r}; each key must equal its name"
            for key, prop in self.properties.items()
            if key != prop.name
        ]
        declared = ", ".join(self.properties) or "none"
        unknown = [name for name in self.entity_key if name not in self.properties]
        if unknown:
            problems.append(
                f"entity_key names undeclared propert(ies) {', '.join(unknown)}; "
                f"declared: {declared}"
            )
        if self.timestamp_field not in self.properties:
            problems.append(
                f"timestamp_field {self.timestamp_field!r} is not a declared property; "
                f"declared: {declared}"
            )
        return problems

    @property
    def property_names(self) -> list[str]:
        """All property names, in declaration order."""
        return list(self.properties.keys())

    def get_property(self, name: str) -> PropertyDefinition | None:
        """Return a property definition by name, or None if it is not declared."""
        return self.properties.get(name)


class IngestInput(BaseModel):
    """
    Input to ``FeatureComponent.prepare_data()``.

    ``source_config`` carries the caller's configuration merged with the
    platform's ``file_contents`` and ``file_utility`` handles. ``config`` is the
    caller's configuration alone, with platform-internal underscore-prefixed
    keys stripped, and ``folder_name`` is lifted out of it for convenience --
    both are populated by the platform on every run.
    """

    source_path: str | None = Field(None, description="Source path or ID (folder_id or space_id)")
    source_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Full source configuration, including file_contents and file_utility",
    )
    operation_type: str = Field(
        "incremental", description="Operation type: 'full' or 'incremental'"
    )
    connection: Any | None = Field(
        None, description="Connection handle from the extension's connect() for external sources"
    )
    resume_from_batch: int = Field(
        0,
        ge=0,
        description="Batch index to resume from (0 = start from the beginning). "
        "Set by the platform when resuming from a checkpoint.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="The caller's ingestion configuration"
    )
    folder_name: str | None = Field(
        None, description="Folder name in organization storage, lifted from config"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class IngestPlan(BaseModel):
    """
    Returned by ``FeatureComponent.prepare_data()``.

    Tells the platform how much work there is, so it can drive the iteration
    loop, report progress, and honour pause/stop/cancel.

    ``checkpoint_interval`` controls how often accumulated records are
    persisted during iteration. On resume, processing continues from the batch
    after the last checkpoint.
    """

    total_items: int = Field(..., description="Total items to process")
    batch_size: int = Field(1000, gt=0, description="Items per batch")
    checkpoint_interval: int = Field(
        1, ge=0, description="Save a checkpoint every N batches (0 = disabled)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata for UI display (e.g. a source description)"
    )

    @property
    def batch_count(self) -> int:
        """Number of batches this plan implies, never fewer than one."""
        return max(1, (self.total_items + self.batch_size - 1) // self.batch_size)


class BatchResult(BaseModel):
    """
    Returned by ``FeatureComponent.process_batch()``.

    Carries the feature records produced by one batch, keyed by feature view
    name. The platform handles storage, serialization, versioning and dedup.
    """

    records: dict[str, list[dict[str, Any]]] = Field(
        ..., description="Feature records keyed by feature view name"
    )
    records_processed: int = Field(..., description="Source items processed in this batch")
    errors: list[str] = Field(default_factory=list, description="Non-fatal errors encountered")


class SampleRequest(BaseModel):
    """
    Request for training samples, passed to ``FeatureComponent.get_samples()``.
    """

    batch_size: int = Field(1000, description="Number of records per batch")
    split: str = Field("train", description="Data split to sample from (train, validation, test)")
    filters: dict[str, Any] = Field(
        default_factory=dict, description="Optional filters to apply to the data"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary parameters passed from the training service"
    )
    source: str = Field(
        "offline", description="Data source: 'offline' (blob storage) or 'online' (Redis)"
    )


class SampleBatch(BaseModel):
    """A batch of training samples yielded by ``FeatureComponent.get_samples()``."""

    records: list[dict[str, Any]] = Field(..., description="Sample records")
    batch_index: int = Field(..., description="Batch index (0-based)")
    total_batches: int | None = Field(None, description="Total number of batches, if known")

    @property
    def record_count(self) -> int:
        """Number of records in this batch."""
        return len(self.records)
