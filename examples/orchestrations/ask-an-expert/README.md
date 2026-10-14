# Ask an Expert

Ask any professional a question. You send a **profession** and a **question**; a ChatGPT agent
answers the way that professional would explain it to a client.

```
Intake (start)  ──▶  Expert (agent, ChatGPT)  ──▶  Reply (end)
```

The smallest useful orchestration: one start, one agent, one end. You need an OpenAI API key.

| Path | What it is |
| --- | --- |
| `ask-an-expert.cborchestration.json` | The import file for the whole orchestration. |
| `intake/`, `expert/`, `reply/` | One folder per component: code (`.py`), ports (`.yaml`), import file (`.cbcomponent.json`) and a README. |
| `run.ipynb` | Runs the orchestration on your machine. |
| `orchestration.yaml`, `perceivo.yaml`, `input.json` | The graph, the local configuration and the sample request the notebook uses. |

## Run it on the platform

1. **Import.** Open **Orchestrations**, choose **Import**, and pick `ask-an-expert.cborchestration.json`.
   The import reports that the orchestration failed validation: no key is bound to the Expert yet,
   and an import file never carries one. Choose **Import as draft**. It opens in the Builder with
   the three components.
2. **Attach the secret.** Select the **Expert** node. In the Inspector, under **Secrets**, pick your
   organization's OpenAI key from the list (or **+ New secret** to add one). The `OPENAI_API_KEY`
   row shows **Bound**.
3. **Parameters.** None. No component in this orchestration takes parameters.
4. **Publish and run.** **File → Publish**, then open **Runner** and run it with the fields from
   `input.json`:

   ```json
   {"profession": "structural engineer", "question": "Why do bridges have expansion joints?"}
   ```

   Open the run's sidebar to see the cost, the latency and the conversation log entry the agent wrote.

## Run it locally with the SDK

1. **Install.**

   ```bash
   pip install perceivo==1.0.0
   ```

2. **Set your key.** Put `OPENAI_API_KEY=<your key>` in a `.env` file in this folder, or set
   `OPENAI_API_KEY` as an environment variable. `perceivo.yaml` reads `.env` first, then the
   environment. `.env` is in `.gitignore`; never commit it.
3. **Run.** Open `run.ipynb` from this folder and run the cells top to bottom. It runs the
   orchestration on `input.json` and shows the answer and the token counts.

`perceivo.yaml` sets `llm: local`, which lets the agent call OpenAI for real. Change it to
`llm: mock` to run offline with a canned reply.

## The port contract

The field names are the contract between components. Intake sends `profession` and `question`;
Expert declares it takes exactly those. Expert sends `answer`, `model`, `provider`, `tokens_in`
and `tokens_out`; Reply declares it takes exactly those and returns them with `total_tokens` added.
`perceivo.orchestration.validate(".")` checks that every edge's two ends agree.
