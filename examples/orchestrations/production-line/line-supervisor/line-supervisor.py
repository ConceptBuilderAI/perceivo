import perceivo
from perceivo.primitives import AgentInput, AgentOutput, Artifact, ArtifactType


@perceivo.agent
async def line_supervisor(input: AgentInput) -> AgentOutput:
    """Call the first stage and wait while the whole line runs."""
    product = input.request_data["product"]
    seconds = input.request_data["seconds_per_stage"]

    perceivo.log.info("Line Supervisor: starting the line", product=product)

    # Cut calls Weld, Weld calls Paint, Paint calls Inspection. This one call
    # returns when Inspection has finished and every stage has handed back.
    result = await perceivo.flow.invoke(
        "first_stage",
        request_data={"product": product, "seconds": seconds, "traveler": ""},
    )
    traveler = result["response_data"]["traveler"]
    passed = result["response_data"]["passed"]
    stages_completed = len(traveler.strip().splitlines())

    perceivo.log.info("Line Supervisor: line finished", stages_completed=stages_completed, passed=passed)

    return AgentOutput(
        artifact=Artifact(type=ArtifactType.TEXT, data=f"{product}\n{traveler}"),
        response_data={"product": product, "stages_completed": stages_completed, "passed": passed},
    )
