# The End

The end of the orchestration. It passes the finished story through as the **artifact** and
summarises what changed in the **response data**.

| | |
| --- | --- |
| Input artifact | `text`: the finished story |
| Input request data | `words_in_opening`, `words_added`, `tokens_in`, `tokens_out` (integers); `model`, `provider` (strings) |
| Output artifact | `text`: the finished story, unchanged |
| Output response data | `total_words`, `words_added` (integers); `model` (string) |
| Secrets | none |
| Parameters | none |

Files: `the-end.py` (code), `the-end.yaml` (ports and fields),
`the-end.cbcomponent.json` (import file for the platform).
