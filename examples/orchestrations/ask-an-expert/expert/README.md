# Expert (agent)

The agent. It works in four steps:

1. reads `profession` and `question` from `input.request_data`;
2. reads the key with `await perceivo.secrets.get("OPENAI_API_KEY")`, never from the code;
3. fills the system prompt from the profession and calls ChatGPT (`gpt-4o-mini`) through
   `ask_chatgpt()`, a plain `httpx` helper that returns the answer, the model and the token counts;
4. returns an `AgentOutput` with the answer as response data (with `model`, `provider`,
   `tokens_in` and `tokens_out`, which the platform uses for cost) and a `ConversationMessage`
   for the conversation log.

| | Field | Type | Description |
| --- | --- | --- | --- |
| In | `profession` | string | The profession the expert speaks as. |
| In | `question` | string | The question to answer. |
| Out | `answer` | string | The expert's reply. |
| Out | `model` | string | The model that wrote the reply. |
| Out | `provider` | string | The model provider (openai). |
| Out | `tokens_in` | integer | Prompt tokens the call used. |
| Out | `tokens_out` | integer | Completion tokens the call used. |

Secrets: `OPENAI_API_KEY` (required), declared in `expert.yaml`. On the platform, bind it in the
Inspector under **Secrets**. Locally, it comes from `.env` or the environment. Parameters: none.

Files: `expert.py` (code), `expert.yaml` (ports and secret), `expert.cbcomponent.json` (import file).
