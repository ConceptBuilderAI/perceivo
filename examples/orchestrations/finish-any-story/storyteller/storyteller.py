import httpx
import perceivo
from perceivo.log.conversation import ContentPart, ConversationMessage
from perceivo.primitives import AgentInput, AgentOutput, Artifact, ArtifactType

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
SYSTEM_PROMPT = (
    "You finish stories. Continue the story you are given in the same voice "
    "and bring it to a satisfying ending in under 200 words. "
    "Reply with the continuation only."
)


async def ask_claude(api_key: str, story_so_far: str) -> dict:
    """Ask Claude to continue the story and return its text with token counts."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            ANTHROPIC_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": MODEL,
                "max_tokens": 600,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": story_so_far}],
            },
        )
        response.raise_for_status()
        body = response.json()

    return {
        "ending": body["content"][0]["text"].strip(),
        "model": body["model"],
        "tokens_in": body["usage"]["input_tokens"],
        "tokens_out": body["usage"]["output_tokens"],
    }


@perceivo.agent
async def storyteller(input: AgentInput) -> AgentOutput:
    """Finish the story carried in the artifact."""
    # The story is the artifact; the word count is request data.
    story_so_far = input.artifact.data
    words_in_opening = input.request_data.get("words_in_opening", 0)

    api_key = await perceivo.secrets.get("ANTHROPIC_API_KEY")
    result = await ask_claude(api_key, story_so_far)
    finished = f"{story_so_far}\n\n{result['ending']}"

    perceivo.log.info("Story finished", words_added=len(result["ending"].split()))

    return AgentOutput(
        # A new artifact: the finished story. Because it changed, the platform snapshots it.
        artifact=Artifact(type=ArtifactType.TEXT, data=finished),
        response_data={
            "words_in_opening": words_in_opening,
            "words_added": len(result["ending"].split()),
            "model": result["model"],
            "provider": "anthropic",
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
        },
        conversation_log=ConversationMessage(
            role="agent",
            parts=[
                ContentPart("text/plain", "Wrote an ending for the story.", name="summary"),
                ContentPart("text/plain", result["ending"], name="response"),
            ],
            metadata={
                "model": result["model"],
                "tokens_in": result["tokens_in"],
                "tokens_out": result["tokens_out"],
            },
        ),
    )
