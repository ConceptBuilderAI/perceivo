# perceivo

Python SDK for building AI orchestration components: create, validate and run
orchestrations locally, then publish them to Perceivo Cloud.

[![PyPI](https://img.shields.io/pypi/v/perceivo.svg)](https://pypi.org/project/perceivo/)
[![Python versions](https://img.shields.io/pypi/pyversions/perceivo.svg)](https://pypi.org/project/perceivo/)
[![CI](https://github.com/ConceptBuilderAI/perceivo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ConceptBuilderAI/perceivo/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

## Installation

```bash
pip install perceivo
```

Optional extras add service clients, for example Redis:

```bash
pip install "perceivo[redis]"
```

The extras are `redis`, `milvus`, `elasticsearch`, `database` and `fastapi`.
Python 3.11, 3.12 and 3.13 are supported.

## Quick start

These commands create a project with a starter orchestration, validate it and
run it locally. They need no account.

<!-- quickstart:begin -->
```bash
perceivo init
perceivo new orchestration my-first-orch
perceivo validate component ./my-first-orch/agent.py ./my-first-orch/agent.yaml
perceivo validate orchestration ./my-first-orch/
perceivo run orchestration ./my-first-orch/ --input '{"query": "test"}'
# expect: RESULT: PASS
```
<!-- quickstart:end -->

### Write your own component

Replace `my-first-orch/agent.py` with:

```python
import perceivo

@perceivo.agent
async def my_agent(input: perceivo.primitives.AgentInput) -> perceivo.primitives.AgentOutput:
    api_key = await perceivo.secrets.get("ANTHROPIC_API_KEY")
    perceivo.log.info("Processing request")

    # Your logic here
    answer = "Hello from perceivo!"

    return perceivo.primitives.AgentOutput(
        artifact=perceivo.primitives.Artifact(
            type=perceivo.primitives.ArtifactType.JSON,
            data={"answer": answer},
        ),
        response_data={"answer": answer, "result": answer},
    )
```

Then run the two `validate` commands and the `run` command again.

## Component Types

| Type | Decorator | Purpose |
|------|-----------|---------|
| **Start** | `@perceivo.start` | Entry point — receives initial input |
| **Agent** | `@perceivo.agent` | Main processor — calls LLMs, makes decisions |
| **Tool** | `@perceivo.tool` | Utility — API calls, data transforms |
| **End** | `@perceivo.end` | Exit point — formats final output |
| **FSM** | `@perceivo.fsm` | State machine — manages state transitions |
| **Policy** | `@perceivo.policy` | Decision maker — controls FSM transitions |
| **Adapter** | `@perceivo.adapter` | Schema transformer — maps between formats |
| **Review** | `@perceivo.review` | Human review — the run waits for a person's decision |

## SDK Modules

The first group is what a component body normally reaches for; the second is the
authoring and local-run side, used by the CLI and by scripts around a project.

| Module | Purpose |
|--------|---------|
| `perceivo.primitives` | Component base classes and their input and output types |
| `perceivo.types` | Shared types: errors, `Page`, enums and records |
| `perceivo.params` | Input parameters declared in the component manifest |
| `perceivo.secrets` | Secret values declared in the manifest's `required_secrets` |
| `perceivo.files` | Organization file storage; `.working` and `.platform` are the other two scopes |
| `perceivo.log` | Structured logging, and log queries by `LogType` with `query` |
| `perceivo.flow` | Port invocation (call connected components) |
| `perceivo.stream` | Conversation streaming — tokens, messages, history |
| `perceivo.alerts` | Custom alerts by key; the platform resolves the audience to subscribed org members |
| `perceivo.cost` | Cost recording and model pricing |
| `perceivo.cluster` | Redis, Milvus, Elasticsearch, and MCP handles (`await ….connect()`) |
| `perceivo.managed_service` | Managed AI models and managed databases |
| `perceivo.intelligence` | Predictions and the feature store of a trained intelligence |
| `perceivo.capabilities` | Capabilities in the organization and their execution graphs |
| `perceivo.capability` | Scoring and evaluator components for a capability's runs |
| `perceivo.reviews` | Completed human-review results |
| `perceivo.component` | Create, validate, import/export, and list components |
| `perceivo.orchestration` | Create, load, validate, run, and publish orchestrations |
| `perceivo.validator` | Validation checks and reports |
| `perceivo.templates` | The component and orchestration template catalog |
| `perceivo.runner` | Local execution and run history |
| `perceivo.bootstrap` | Session lifecycle for a run |
| `perceivo.local` | Local mode, per-service mock modes, and the call trace |
| `perceivo.config` | `perceivo.yaml` loading |

For HTTP requests to external APIs (LLMs, etc.), use `httpx` or the provider's
Python SDK directly. Retrieve API keys with `perceivo.secrets.get()`.

## Configuration

Create a `perceivo.yaml` in your project:

```yaml
version: 1

files:
  working_dir: ./working_files
  org_dir: ./org_files

secrets:
  source: dotenv
  dotenv_path: .env

redis:
  enabled: false
  url: redis://localhost:6379

logging:
  level: INFO
  format: console
```

Secrets are read from `.env` in local mode. In Perceivo Cloud, they are
resolved from the configured managed secret service.

## Local vs Cloud

Local runs exercise the same component interface through local adapters and
configured mocks. Cloud runs use managed services and distributed execution,
so credentials, service availability, networking, and performance differ even
when the component code stays the same. See the
[documentation](https://perceivo.ai/docs) for adapter and mocking guidance.

| Feature | Local | Cloud |
|---------|-------|-------|
| Secrets | `.env` file | Azure Key Vault |
| Files | Local filesystem | Azure Blob Storage |
| Logging | Console | Loki |
| HTTP | Direct requests | Egress broker |
| Redis | Local/in-memory | Azure Redis |
| Execution | Single process | Managed distributed runtime |

## Documentation

The complete documentation is at [perceivo.ai/docs](https://perceivo.ai/docs).
The [examples](examples/orchestrations) directory holds three complete
orchestrations, each exactly as it downloads from the website: an import file,
one folder per component and a notebook that runs it on your machine.

## Support and security

- Questions and bugs: open an [issue](https://github.com/ConceptBuilderAI/perceivo/issues),
  or email support@perceivo.ai.
- Security vulnerabilities: do not open a public issue; follow [SECURITY.md](SECURITY.md).
- Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
  [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Apache 2.0 — see [LICENSE](LICENSE).
