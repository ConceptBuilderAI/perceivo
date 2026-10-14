"""Provide the shared types for the perceivo SDK.

Five families live here:

* **Intelligence**: the typed pairs for the four intelligence components in
  :mod:`perceivo.intelligence`: :mod:`.features`, :mod:`.training`,
  :mod:`.inference` and :mod:`.evaluation`.
* **Vocabulary**: the :class:`~enum.StrEnum` types in :mod:`.enums` that more
  than one plane uses.
* **Plane-owned results**: the shapes verbs return, in :mod:`.records`.
* **Envelope and errors**: :class:`.Page` and the seven exception types.
* **Credentials**: :class:`.Credentials`, the public-API credential in one of
  three authentication modes.

The orchestration primitives' input and output types, and :class:`Artifact`,
live in :mod:`perceivo.primitives`, next to the classes whose signatures they
type. A type that belongs to one plane lives on that plane
(``perceivo.log.LogType``, ``perceivo.files.StorageContainer``,
``perceivo.reviews.ReviewSubject``, ``perceivo.alerts.AlertDelivery``).
"""

from ._credentials import Credentials
from .enums import AIService, Level, Runner, Severity
from .errors import (
    AccessDeniedError,
    AuthenticationError,
    ConfigurationError,
    ContextError,
    NotFoundError,
    PerceivoError,
    ServiceError,
)
from .evaluation import (
    EvaluationAction,
    EvaluationActionType,
    EvaluationConfig,
    EvaluationContext,
    MetricDefinition,
    MetricType,
    OfflineEvaluationInput,
    OfflineEvaluationOutput,
    OnlineEvaluationInput,
    OnlineEvaluationOutput,
    PromotionDecision,
)
from .features import (
    BatchResult,
    FeatureSchema,
    IngestInput,
    IngestPlan,
    PropertyDefinition,
    PropertyType,
    SampleBatch,
    SampleRequest,
)
from .inference import InferenceInput, InferenceOutput
from .pagination import Page
from .records import (
    FileEntry,
    ModelPricing,
    ParamDeclaration,
    Prediction,
    SecretDeclaration,
)
from .training import EvaluationResult, IterationResult, TrainingPlan

__all__ = [
    "AIService",
    "AccessDeniedError",
    "AuthenticationError",
    "BatchResult",
    "ConfigurationError",
    "ContextError",
    "Credentials",
    "EvaluationAction",
    "EvaluationActionType",
    "EvaluationConfig",
    "EvaluationContext",
    "EvaluationResult",
    "FeatureSchema",
    "FileEntry",
    "InferenceInput",
    "InferenceOutput",
    "IngestInput",
    "IngestPlan",
    "IterationResult",
    "Level",
    "MetricDefinition",
    "MetricType",
    "ModelPricing",
    "NotFoundError",
    "OfflineEvaluationInput",
    "OfflineEvaluationOutput",
    "OnlineEvaluationInput",
    "OnlineEvaluationOutput",
    "Page",
    "ParamDeclaration",
    "PerceivoError",
    "Prediction",
    "PromotionDecision",
    "PropertyDefinition",
    "PropertyType",
    "Runner",
    "SampleBatch",
    "SampleRequest",
    "SecretDeclaration",
    "ServiceError",
    "Severity",
    "TrainingPlan",
]
