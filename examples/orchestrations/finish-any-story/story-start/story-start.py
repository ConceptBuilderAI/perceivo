import perceivo
from perceivo.primitives import Artifact, ArtifactType, StartInput, StartOutput


@perceivo.start
async def story_start(input: StartInput) -> StartOutput:
    """Make the opening lines the story artifact, and count its words."""
    opening = input.request_data.get("opening", "").strip()
    if not opening:
        raise ValueError("'opening' is required.")

    return StartOutput(
        # The artifact carries the story to the next component.
        artifact=Artifact(type=ArtifactType.TEXT, data=opening),
        # Response data carries facts about it.
        response_data={"words_in_opening": len(opening.split())},
    )
