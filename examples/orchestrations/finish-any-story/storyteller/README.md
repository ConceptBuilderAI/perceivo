# Storyteller

The agent. It reads the story from the **artifact** and the word count from the request data,
asks Claude (`claude-haiku-4-5-20251001`) for an ending of under 200 words, and returns a **new**
artifact holding the whole story. Because the artifact changed, the platform snapshots it. It also
returns token counts and a conversation log entry with the ending.

| | |
| --- | --- |
| Input artifact | `text`: the story so far |
| Input request data | `words_in_opening` (integer) |
| Output artifact | `text`: the finished story |
| Output response data | `words_in_opening`, `words_added`, `tokens_in`, `tokens_out` (integers); `model`, `provider` (strings) |
| Secrets | `ANTHROPIC_API_KEY` (required) |
| Parameters | none |

The key is read at run time with `perceivo.secrets.get("ANTHROPIC_API_KEY")`; it is never stored in
the component. On the platform, bind it in the Inspector under **Secrets**. Locally, put it in
`.env` or the environment.

Files: `storyteller.py` (code), `storyteller.yaml` (ports and fields),
`storyteller.cbcomponent.json` (import file for the platform).
