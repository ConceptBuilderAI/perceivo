import httpx
import perceivo
from perceivo.log.conversation import ContentPart, ConversationMessage
from perceivo.primitives import AgentInput, AgentOutput, Artifact, ArtifactType

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"


async def ask_chatgpt(api_key: str, system_prompt: str, question: str) -> dict:
    """Send one question to ChatGPT and return the reply with its token counts."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
            },
        )
        response.raise_for_status()
        body = response.json()

    return {
        "answer": body["choices"][0]["message"]["content"],
        "model": body["model"],
        "tokens_in": body["usage"]["prompt_tokens"],
        "tokens_out": body["usage"]["completion_tokens"],
    }


@perceivo.agent
async def expert(input: AgentInput) -> AgentOutput:
    """Answer the question as the requested professional."""
    # 1. Read the request data the start component sent.
    profession = input.request_data["profession"]
    question = input.request_data["question"]

    # 2. Read the secret declared in expert.yaml.
    api_key = await perceivo.secrets.get("OPENAI_API_KEY")

    # 3. Fill the system prompt from the request data, then call ChatGPT.
    system_prompt = (
        f"You are an experienced {profession}. "
        "Answer the question the way you would explain it to a client: "
        "plainly, accurately and in under 150 words."
    )
    result = await ask_chatgpt(api_key, system_prompt, question)
    perceivo.log.info("Expert answered", model=result["model"], tokens_out=result["tokens_out"])

    # 4. Return two things: the reply as response data, and an entry for the conversation log.
    return AgentOutput(
        artifact=Artifact(type=ArtifactType.TEXT, data=result["answer"]),
        response_data={
            "answer": result["answer"],
            "model": result["model"],
            "provider": "openai",
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
        },
        conversation_log=ConversationMessage(
            role="agent",
            parts=[
                ContentPart("text/plain", f"Answered as a {profession}.", name="summary"),
                ContentPart("text/plain", result["answer"], name="response"),
            ],
            metadata={
                "model": result["model"],
                "tokens_in": result["tokens_in"],
                "tokens_out": result["tokens_out"],
            },
        ),
    )
