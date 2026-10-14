# Intake (start)

The start component. It reads `profession` and `question` from the request, strips surrounding
spaces, and stops the run with an error if either one is empty. It passes both on unchanged as its
response data, and sends the question out as a text artifact.

| | Field | Type | Description |
| --- | --- | --- | --- |
| In (request) | `profession` | string | Who should answer, for example "structural engineer". |
| In (request) | `question` | string | The question to ask that expert. |
| Out | `profession` | string | The profession, forwarded unchanged. |
| Out | `question` | string | The question, forwarded unchanged. |

Secrets: none. Parameters: none.

Files: `intake.py` (code), `intake.yaml` (ports), `intake.cbcomponent.json` (import file).
