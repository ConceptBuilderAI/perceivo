# Changelog

All notable changes to the perceivo SDK are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-13

The first stable release of `perceivo`, the Python SDK for writing components
that run on the Perceivo platform. Requires Python 3.11 or later.

### Added

- **Authoring decorators.** Eight component types, each with a decorator and a
  base class: `@agent`, `@tool`, `@fsm`, `@policy`, `@adapter`, `@review`,
  `@start` and `@end`, with a typed input and output pair for each in
  `perceivo.primitives`.
- **Machine learning and capability extensions.** `FeatureComponent`,
  `TrainingComponent`, `InferenceComponent` and `EvaluationComponent` in
  `perceivo.intelligence`, and scoring and evaluator base classes in
  `perceivo.capability`.
- **Orchestration primitives.** Port-addressed calls between nodes with
  `perceivo.flow`, interactive streaming with `perceivo.stream`, and durable
  conversation logs for tracing an agent's reasoning across a run.
- **Configuration and secrets.** Declared parameters through `perceivo.params`,
  and secrets resolved by name at call time through `perceivo.secrets`, never
  placed in the process environment.
- **Files.** The organization's file store through `perceivo.files`, read-only
  system containers through `perceivo.files.platform`, and per-run scratch space
  through `perceivo.files.working`.
- **Managed and cluster services.** Model invocation (chat, embeddings, content
  safety and groundedness) and managed database queries through
  `perceivo.managed_service`, and connections to Milvus, Elasticsearch, Redis and
  MCP through `perceivo.cluster`. The other `AIService` members are reserved and
  raise `NotImplementedError`.
- **Intelligence and reviews.** Predictions and prediction history through
  `perceivo.intelligence.inference`, the feature store through
  `perceivo.intelligence.features`, and completed human-review results through
  `perceivo.reviews`.
- **Observability.** Structured logging and log queries with `perceivo.log`,
  cost attribution and model pricing with `perceivo.cost`, and subscription-based
  alerts and direct notifications with `perceivo.alerts`.
- **Validator.** Static validation of components and orchestrations with
  machine-readable reports, through `perceivo.validator`. `perceivo validate`
  fails a component that names an SDK attribute or module the installed SDK
  does not provide (`VAL-IMPORT`), reporting the error the run would raise at
  load.
- **Local runner.** Single-process local execution of components and
  orchestrations, with per-service mock or local backends, a mock trace, event
  streaming and run history, through `perceivo.runner`, `perceivo.local`,
  `perceivo.config` and `perceivo.bootstrap`.
- **CLI.** The `perceivo` command: `init`, `new`, `validate`, `run`, `mock`,
  `import-bundle`, `export` and `publish`.
- **Templates.** A catalog of component and orchestration templates through
  `perceivo.templates` and `perceivo new --template`.
- **Publishing.** `perceivo.orchestration.publish` and `perceivo publish`, with
  API key, HMAC-signed API key and Microsoft Entra ID authentication, and a
  dry-run mode that builds the bundle without uploading it.
- **Typed errors and pagination.** A single `PerceivoError` hierarchy with
  retryable service errors, and a `Page[T]` type for paginated results.

### Repository

- **Examples.** Three orchestration packages under `examples/orchestrations/`,
  each byte-identical to its published download, with its import file,
  component folders and a notebook that runs it locally.

[1.0.0]: https://github.com/ConceptBuilderAI/perceivo/releases/tag/v1.0.0
