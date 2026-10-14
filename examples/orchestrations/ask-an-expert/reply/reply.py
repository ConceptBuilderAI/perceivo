import perceivo
from perceivo.primitives import EndInput, EndOutput


@perceivo.end
async def reply(input: EndInput) -> EndOutput:
    """Return the answer, the model and the token counts to the caller."""
    tokens_in = input.response_data.get("tokens_in", 0)
    tokens_out = input.response_data.get("tokens_out", 0)

    return EndOutput(
        artifact=input.artifact,
        response_data={
            "answer": input.response_data.get("answer", ""),
            "model": input.response_data.get("model", ""),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )
