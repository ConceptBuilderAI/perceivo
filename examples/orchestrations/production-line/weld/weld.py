import asyncio

import perceivo
from perceivo.primitives import ToolInput, ToolOutput


@perceivo.tool
async def weld(input: ToolInput) -> ToolOutput:
    """Stage 2: welding the frame, then hand the work order to Paint."""
    product = input.request_data["product"]
    seconds = input.request_data["seconds"]
    traveler = input.request_data["traveler"]

    perceivo.log.info("Weld: welding the frame", product=product, seconds=seconds)
    await asyncio.sleep(seconds)
    traveler = traveler + "Weld: done\n"

    # Call the next stage through this tool's own tool port, and wait for it.
    result = await perceivo.flow.invoke(
        "next_stage",
        request_data={"product": product, "seconds": seconds, "traveler": traveler},
    )
    # Pass the next stage's answer back up the line, field by field.
    answer = result["response_data"]
    return ToolOutput(response_data={"traveler": answer["traveler"], "passed": answer["passed"]})
