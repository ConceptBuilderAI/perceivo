import asyncio

import perceivo
from perceivo.primitives import ToolInput, ToolOutput


@perceivo.tool
async def cut(input: ToolInput) -> ToolOutput:
    """Stage 1: cutting the parts, then hand the work order to Weld."""
    product = input.request_data["product"]
    seconds = input.request_data["seconds"]
    traveler = input.request_data["traveler"]

    perceivo.log.info("Cut: cutting the parts", product=product, seconds=seconds)
    await asyncio.sleep(seconds)
    traveler = traveler + "Cut: done\n"

    # Call the next stage through this tool's own tool port, and wait for it.
    result = await perceivo.flow.invoke(
        "next_stage",
        request_data={"product": product, "seconds": seconds, "traveler": traveler},
    )
    # Pass the next stage's answer back up the line, field by field.
    answer = result["response_data"]
    return ToolOutput(response_data={"traveler": answer["traveler"], "passed": answer["passed"]})
