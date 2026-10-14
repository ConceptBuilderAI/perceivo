"""
Base classes for intelligence components.

Where the orchestration primitives in :mod:`perceivo.primitives` run a
single step of a graph, these four run the stages of an intelligence: ingest
features, train a model, serve predictions, evaluate what came out.

The platform owns every loop. A component declares what one unit of work is
and the platform drives it: reporting progress, checkpointing, honouring
pause/stop/cancel between units, and writing results to storage. Work is split
into units because a method that runs to completion cannot be paused, resumed
or reported on.

Example:
    ```python
    import perceivo
    from perceivo.intelligence import TrainingComponent
    from perceivo.types import EvaluationResult, IterationResult, TrainingPlan

    class MyTraining(TrainingComponent):
        async def initialize(self, train_data, val_data, hyperparameters):
            self.model = build_model(hyperparameters)
            return TrainingPlan(total_iterations=hyperparameters.get("epochs", 10))

        async def run_iteration(self, iteration: int) -> IterationResult:
            return IterationResult(metrics={"loss": self._train_one_epoch()})

        async def evaluate(self) -> EvaluationResult:
            return EvaluationResult(metrics={"accuracy": self._accuracy()})

    ENTRY_CLASS = "MyTraining"
    ```
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..types.inference import InferenceOutput

if TYPE_CHECKING:
    from ..types import (
        BatchResult,
        EvaluationConfig,
        EvaluationResult,
        FeatureSchema,
        InferenceInput,
        IngestInput,
        IngestPlan,
        IterationResult,
        OfflineEvaluationOutput,
        OnlineEvaluationOutput,
        PromotionDecision,
        SampleBatch,
        SampleRequest,
        TrainingPlan,
    )


class FeatureComponent(ABC):
    """
    Base class for FEATURE components.

    Feature components declare an intelligence's feature schema, ingest source
    data into the feature store, and stream samples back out for training.

    Implement four methods:

    1. ``get_schema()``: declare the feature schema
    2. ``prepare_data()``: discover the work, return a plan
    3. ``process_batch()``: produce one batch of feature records
    4. ``get_samples()``: yield batches of samples for training

    The platform calls ``prepare_data()`` once, then ``process_batch()`` for
    each batch in the plan, checkpointing and checking for control commands
    between calls. The component holds its own state on ``self`` between them.

    Example:
        ```python
        class MovieFeatures(perceivo.intelligence.FeatureComponent):
            def get_schema(self) -> perceivo.types.FeatureSchema:
                return perceivo.types.FeatureSchema(
                    properties={
                        "user_id": perceivo.types.PropertyDefinition(
                            name="user_id", dtype=perceivo.types.PropertyType.STRING),
                        "event_timestamp": perceivo.types.PropertyDefinition(
                            name="event_timestamp",
                            dtype=perceivo.types.PropertyType.TIMESTAMP),
                    },
                    entity_key=["user_id"],
                    timestamp_field="event_timestamp",
                )

            async def prepare_data(self, input: perceivo.types.IngestInput):
                files = await perceivo.files.list(parent_id=input.source_path)
                self._data = await self._load(files)
                return perceivo.types.IngestPlan(total_items=len(self._data), batch_size=1000)

            async def process_batch(self, batch_index: int):
                batch = self._data[batch_index * 1000:(batch_index + 1) * 1000]
                return perceivo.types.BatchResult(
                    records={"user_features": [transform(i) for i in batch]},
                    records_processed=len(batch),
                )

            async def get_samples(self, request: perceivo.types.SampleRequest):
                for i, chunk in enumerate(self._chunks(request.batch_size)):
                    yield perceivo.types.SampleBatch(records=chunk, batch_index=i)
        ```
    """

    @abstractmethod
    def get_schema(self) -> "FeatureSchema":
        """
        Declare the feature schema for this intelligence.

        Returns:
            FeatureSchema with the properties, entity key and timestamp field.
        """
        raise NotImplementedError("Subclass must implement get_schema()")

    @abstractmethod
    async def prepare_data(self, input: "IngestInput") -> "IngestPlan":
        """
        Discover the source data and report how much work there is.

        Called once at the start of ingestion. Download or open the source,
        hold whatever state ``process_batch()`` will need on ``self``, and
        return a plan the platform can iterate.

        Args:
            input: IngestInput with the source path, configuration and, when
                resuming, the batch index to continue from.

        Returns:
            IngestPlan with the total item count and batch size.
        """
        raise NotImplementedError("Subclass must implement prepare_data()")

    @abstractmethod
    async def process_batch(self, batch_index: int) -> "BatchResult":
        """
        Produce the feature records for one batch.

        The platform calls this once per batch in the plan, reporting progress
        and checking for pause/stop/cancel between calls. Storage,
        serialization, versioning and dedup are the platform's job.

        Args:
            batch_index: Zero-based index of the batch to process.

        Returns:
            BatchResult with records keyed by feature view name.
        """
        raise NotImplementedError("Subclass must implement process_batch()")

    @abstractmethod
    async def get_samples(self, request: "SampleRequest") -> AsyncIterator["SampleBatch"]:
        """
        Yield batches of training samples.

        Implement this as an async generator. The training service consumes it
        with ``async for``, so a component can stream from the feature store
        without materializing the whole split in memory.

        Args:
            request: SampleRequest with the batch size, split and filters.

        Yields:
            SampleBatch objects.
        """
        raise NotImplementedError("Subclass must implement get_samples()")

    async def prepare_materialize(
        self,
        records: list[Any],
        parameters: dict[str, Any],
    ) -> list[Any]:
        """
        Optional: filter or transform records before materialization to Redis.

        Called during materialization with every record from the offline store
        and the caller's materialization parameters. Return the subset to write
        to the online store. The default returns all records unchanged.

        Args:
            records: Flat feature record dicts from the offline store.
            parameters: Caller-supplied materialization parameters.

        Returns:
            The records to write to the online store.
        """
        return records

    async def generate_inference_sample(self) -> dict[str, Any] | None:
        """
        Optional: build a sample prediction request for testing inference.

        Return a request body that can be sent straight to the intelligence's
        inference endpoint, e.g. ``{"features": {"user_id": "user_123"}}``. The
        extension can read ``perceivo.intelligence.features`` to build it from real
        feature data. The default returns None.

        Returns:
            A prediction request body, or None if not implemented.
        """
        return None


class TrainingComponent(ABC):
    """
    Base class for TRAINING components.

    Training components build a model, train it one iteration at a time, and
    report final metrics.

    Implement three methods:

    1. ``initialize()``: set up model, optimizer and data; return a plan
    2. ``run_iteration()``: train one iteration, return its metrics
    3. ``evaluate()``: compute the final metrics

    Assign every declared artifact as ``self.<attribute>`` in ``initialize()``.
    The platform serializes those attributes according to the artifact
    declarations when training completes, and handles checkpointing, progress
    reporting, early stopping and control commands between iterations.

    Example:
        ```python
        class NCFTraining(perceivo.intelligence.TrainingComponent):
            async def initialize(self, train_data, val_data, hyperparameters):
                self.user_id_map = build_map(train_data, "user_id")
                self.model = NCF(len(self.user_id_map), hyperparameters["dim"])
                return perceivo.types.TrainingPlan(total_iterations=hyperparameters["epochs"])

            async def run_iteration(self, iteration: int):
                return perceivo.types.IterationResult(metrics={"train_loss": self._epoch()})

            async def evaluate(self):
                return perceivo.types.EvaluationResult(metrics={"ndcg@10": self._ndcg()})

        ENTRY_CLASS = "NCFTraining"
        ```
    """

    @abstractmethod
    async def initialize(
        self,
        train_data: Any,
        val_data: Any,
        hyperparameters: dict[str, Any],
    ) -> "TrainingPlan":
        """
        Initialize training.

        Must assign all declared artifacts as instance attributes; the
        platform serializes them by attribute name when training completes.

        Args:
            train_data: Training data from the feature service (list of dicts).
            val_data: Validation data from the feature service (list of dicts).
            hyperparameters: The caller's hyperparameters.

        Returns:
            TrainingPlan with the total iteration count and iteration name.
        """
        raise NotImplementedError("Subclass must implement initialize()")

    @abstractmethod
    async def run_iteration(self, iteration: int) -> "IterationResult":
        """
        Train for one iteration.

        The platform calls this in a loop, reporting progress after each call,
        checking control commands between calls, saving checkpoints on its own
        schedule, and early-stopping on the metrics returned here.

        Args:
            iteration: Zero-based iteration number.

        Returns:
            IterationResult with this iteration's metrics, and ``is_complete``
            set if the component has converged early.
        """
        raise NotImplementedError("Subclass must implement run_iteration()")

    @abstractmethod
    async def evaluate(self) -> "EvaluationResult":
        """
        Evaluate the model on validation data.

        Called once after training completes or early-stops. These metrics are
        saved as the run's final training metrics and are what the evaluation
        stage compares against the champion.

        Returns:
            EvaluationResult with the final metrics.
        """
        raise NotImplementedError("Subclass must implement evaluate()")


class InferenceComponent(ABC):
    """
    Base class for INFERENCE components.

    Inference components serve predictions from a trained model.

    Implement ``predict()``. The platform loads every declared artifact onto
    the instance before the first call, so ``self.model`` is the entry-point
    artifact and each other artifact is available under its declared attribute
    name. ``self._model_version`` carries the version from the manifest.

    Override ``create_model()`` only when the entry-point artifact is a
    ``pytorch_state_dict`` and the platform needs an instance to load into.
    Override ``postprocess()`` to shape the response; the default wraps the
    prediction in an ``InferenceOutput``.

    Set ``async_mode = True`` for long-running predictions such as image
    generation. Requests are then queued and processed in the background, and
    clients poll for status and download results when complete.

    Example:
        ```python
        class MyInference(perceivo.intelligence.InferenceComponent):
            async def predict(self, input: perceivo.types.InferenceInput):
                user = self.user_id_map[input.features["user_id"]]
                return {"scores": self.model.rank(user)}
        ```
    """

    # Set by the platform from the model manifest before predict() is called.
    _model_version: str = "1.0"

    # Set to True for long-running predictions that should use the async
    # queue/poll pattern instead of synchronous request/response.
    async_mode: bool = False

    def create_model(self, config: dict[str, Any]) -> Any:
        """
        Create a model instance for loading a state dict into.

        Override this only when the entry-point artifact is a
        ``pytorch_state_dict``.

        Args:
            config: The artifact's declared model configuration.

        Returns:
            An uninitialized model instance.
        """
        raise NotImplementedError(
            "Must implement create_model() when using pytorch_state_dict artifact type."
        )

    @abstractmethod
    async def predict(self, input: "InferenceInput") -> Any:
        """
        Run inference.

        Every declared artifact is already loaded as an instance attribute --
        ``self.model`` plus whatever else the manifest declared.

        Args:
            input: InferenceInput with the request features and metadata.

        Returns:
            The prediction, in any shape. ``postprocess()`` wraps it.
        """
        raise NotImplementedError("Subclass must implement predict()")

    async def postprocess(self, prediction: Any, input: "InferenceInput") -> InferenceOutput:
        """
        Transform a prediction into the response.

        Override for custom formatting. The default wraps the prediction and
        stamps the serving model's version.

        Args:
            prediction: Whatever ``predict()`` returned.
            input: The InferenceInput that produced it.

        Returns:
            InferenceOutput.
        """
        return InferenceOutput(
            result=prediction,
            model_version=self._model_version,
        )


class EvaluationComponent(ABC):
    """
    Base class for EVALUATION components, which decide what happens to a model after it is
    trained and watch how it behaves once it is serving.

    Implement four methods:

    1. ``get_evaluation_config()``: declare the metrics and thresholds
    2. ``offline_evaluation()``: compare candidates, return actions
    3. ``online_evaluation()``: propose a family's traffic from live statistics
    4. ``should_promote()``: required by the platform's loader, which doesn't call it

    A component returns *actions*; the platform is what promotes, demotes, re-weights traffic and
    raises alerts. Every action carries a reason because every action is surfaced to an operator.

    Example:
        ```python
        class MyEvaluation(perceivo.intelligence.EvaluationComponent):
            def get_evaluation_config(self) -> perceivo.types.EvaluationConfig:
                return perceivo.types.EvaluationConfig(metrics=[
                    perceivo.types.MetricDefinition(
                        name="ndcg@10",
                        metric_type=perceivo.types.MetricType.HIGHER_IS_BETTER,
                        threshold=0.3,
                    )
                ])

            async def offline_evaluation(
                self, training_results, champion_metrics, config, family_config
            ):
                best = max(training_results, key=lambda r: r["metrics"]["ndcg@10"])
                return perceivo.types.OfflineEvaluationOutput(model_actions=[
                    perceivo.types.EvaluationAction(
                        action_type=perceivo.types.EvaluationActionType.PROMOTE_MODEL,
                        model_id=best["model_id"],
                        to_status="canary",
                        reason="Best ndcg@10 among candidates",
                    )
                ])

            async def online_evaluation(
                self, current_traffic_plan, model_stats, config, family_config
            ):
                return perceivo.types.OnlineEvaluationOutput()  # keep the current traffic

            async def should_promote(self, evaluation: perceivo.types.OfflineEvaluationOutput):
                return perceivo.types.PromotionDecision(should_promote=False)  # hold by default
        ```
    """

    @abstractmethod
    def get_evaluation_config(self) -> "EvaluationConfig":
        """
        Declare the metrics this intelligence is evaluated on.

        Returns:
            EvaluationConfig with the metric definitions and promotion
            thresholds.
        """
        raise NotImplementedError("Subclass must implement get_evaluation_config()")

    @abstractmethod
    async def offline_evaluation(
        self,
        training_results: list[dict[str, Any]],
        champion_metrics: dict[str, Any],
        config: dict[str, Any],
        family_config: dict[str, Any],
    ) -> "OfflineEvaluationOutput":
        """
        Evaluate freshly trained candidates against the current champion.

        Called after training completes, once per model family, with these
        keywords (modelled by ``OfflineEvaluationInput``): ``training_results``,
        this family's, each with a ``model_id`` and its metrics;
        ``champion_metrics``, empty when there is no champion; ``config``; and
        ``family_config``. Returns the actions for the platform to take.
        """
        raise NotImplementedError("Subclass must implement offline_evaluation()")

    @abstractmethod
    async def online_evaluation(
        self,
        current_traffic_plan: dict[str, Any],
        model_stats: dict[str, Any],
        config: dict[str, Any],
        family_config: dict[str, Any],
    ) -> "OnlineEvaluationOutput":
        """
        Propose one model family's traffic from its live statistics.

        Called once per family with the keywords ``OnlineEvaluationInput`` models;
        ``model_stats`` is keyed by ``model_id``. The platform reads only the
        fields of ``OnlineEvaluationOutput``.
        """
        raise NotImplementedError("Subclass must implement online_evaluation()")

    @abstractmethod
    async def should_promote(self, evaluation: "OfflineEvaluationOutput") -> "PromotionDecision":
        """
        Decide whether a candidate is promoted. The platform's extension loader
        requires this method but doesn't call it.

        Args:
            evaluation: The output of ``offline_evaluation()``.

        Returns:
            PromotionDecision with the decision, target status and reason.
        """
        raise NotImplementedError("Subclass must implement should_promote()")
