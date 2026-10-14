# Reply (end)

The end component. It reads the agent's response data and returns the answer, the model and the
token counts to the caller, with `total_tokens` added. The agent's text artifact passes through
unchanged.

| | Field | Type | Description |
| --- | --- | --- | --- |
| In | `answer` | string | The expert's reply. |
| In | `model` | string | The model that wrote the reply. |
| In | `provider` | string | The model provider. |
| In | `tokens_in` | integer | Prompt tokens the call used. |
| In | `tokens_out` | integer | Completion tokens the call used. |
| Out (result) | `answer` | string | The expert's reply. |
| Out (result) | `model` | string | The model that wrote the reply. |
| Out (result) | `tokens_in` | integer | Prompt tokens the call used. |
| Out (result) | `tokens_out` | integer | Completion tokens the call used. |
| Out (result) | `total_tokens` | integer | `tokens_in` plus `tokens_out`. |

Secrets: none. Parameters: none.

Files: `reply.py` (code), `reply.yaml` (ports), `reply.cbcomponent.json` (import file).
