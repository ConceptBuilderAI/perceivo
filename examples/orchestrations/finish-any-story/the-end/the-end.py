import perceivo
from perceivo.primitives import EndInput, EndOutput


@perceivo.end
async def the_end(input: EndInput) -> EndOutput:
    """Return the finished story and a summary of what changed."""
    words_in_opening = input.response_data.get("words_in_opening", 0)
    words_added = input.response_data.get("words_added", 0)

    return EndOutput(
        # The finished story goes back to the caller as the artifact.
        artifact=input.artifact,
        response_data={
            "total_words": words_in_opening + words_added,
            "words_added": words_added,
            "model": input.response_data.get("model", ""),
        },
    )
