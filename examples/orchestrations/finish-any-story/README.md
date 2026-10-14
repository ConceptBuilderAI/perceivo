# Finish Any Story

Type the opening of a story; a Claude agent writes the ending.

```
Story Start (start)  ──▶  Storyteller (agent, Claude)  ──▶  The End (end)
```

This example is about the two things that flow between components:

- The **artifact** is the work itself: here, the story text. A component passes it on, or replaces it.
- **Response data** is small, named facts about the work: word counts, the model, token counts.

When an agent replaces the artifact, the platform keeps a **snapshot** of each version, so you can
compare the story before and after Claude touched it.

You need an Anthropic API key.

## What is in this folder

| Path | What it is |
| --- | --- |
| `finish-any-story.cborchestration.json` | The import file for the platform. |
| `run.ipynb` | Runs the orchestration locally with the SDK. |
| `orchestration.yaml`, `perceivo.yaml`, `input.json` | The graph, the local configuration and a sample input, used by the local run. |
| `story-start/`, `storyteller/`, `the-end/` | One folder per component: its code, its YAML, its import file and a README. |

## Run it on the platform

1. **Import.** Open **Orchestrations**, choose **Import**, and pick
   `finish-any-story.cborchestration.json`. The import reports that the orchestration failed
   validation, because no key is bound to the Storyteller yet. Choose **Import as draft**.
2. **Attach the secret.** Select the **Storyteller** node. In the Inspector, under **Secrets**, pick
   your organization's Anthropic key from the list (or **+ New secret** to add one). The
   `ANTHROPIC_API_KEY` row shows **Bound**. The Storyteller is the only component that reads it.
3. **Parameters.** None. No component in this example takes parameters.
4. **Publish and run.** **File → Publish**, then open **Runner** and run it with an opening line,
   for example the one in `input.json`.

In the run, compare the **artifact** (the whole story) with the **response data** (the counts),
open the **snapshots** to see the story before and after the Storyteller, and read the **logs**.

## Run it locally with the SDK

1. Install the SDK:

   ```bash
   pip install perceivo==1.0.0
   ```

2. Set your key. Either put `ANTHROPIC_API_KEY=<your key>` in a `.env` file in this folder, or set
   `ANTHROPIC_API_KEY` as an environment variable. `perceivo.yaml` reads `.env` first, then the
   environment. `.env` is git-ignored; never commit it.
3. Open `run.ipynb` from this folder and run the cells top to bottom. It runs the story in
   `input.json` and prints the artifact and the response data separately.

`perceivo.yaml` sets `llm: local`, which lets the agent call Anthropic for real. Change it to
`llm: mock` to run offline with a canned reply.

## The components

- [`story-start/`](story-start/README.md) puts the opening into the artifact and its word count
  into the response data.
- [`storyteller/`](storyteller/README.md) reads the story from the artifact, asks Claude for an
  ending, and returns a **new** artifact holding the whole story. That change is what the platform
  snapshots.
- [`the-end/`](the-end/README.md) returns the finished story and reports `total_words`,
  `words_added` and `model`.
