# Story Start

The start of the orchestration. It takes the opening lines you type, makes them the story
**artifact** (text), and puts their word count into the **response data**. It fails if `opening`
is empty.

| | |
| --- | --- |
| Input | request data `opening` (string): the first lines of a story |
| Output artifact | `text`: the opening |
| Output response data | `words_in_opening` (integer) |
| Secrets | none |
| Parameters | none |

Files: `story-start.py` (code), `story-start.yaml` (ports and fields),
`story-start.cbcomponent.json` (import file for the platform).
