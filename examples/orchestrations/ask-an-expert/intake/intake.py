import perceivo
from perceivo.primitives import Artifact, ArtifactType, StartInput, StartOutput


@perceivo.start
async def intake(input: StartInput) -> StartOutput:
    """Read the two inputs, check them, and pass them to the expert."""
    profession = input.request_data.get("profession", "").strip()
    question = input.request_data.get("question", "").strip()

    if not profession or not question:
        raise ValueError("Both 'profession' and 'question' are required.")

    perceivo.log.info("Intake received a question", profession=profession)

    return StartOutput(
        artifact=Artifact(type=ArtifactType.TEXT, data=question),
        response_data={"profession": profession, "question": question},
    )
