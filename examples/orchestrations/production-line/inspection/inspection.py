import asyncio

import perceivo
from perceivo.primitives import ToolInput, ToolOutput


@perceivo.tool
async def inspection(input: ToolInput) -> ToolOutput:
    """Stage 4: inspecting the finished product. This is the last stage, so it returns the result."""
    product = input.request_data["product"]
    seconds = input.request_data["seconds"]
    traveler = input.request_data["traveler"]

    perceivo.log.info("Inspection: inspecting the finished product", product=product, seconds=seconds)
    await asyncio.sleep(seconds)
    traveler = traveler + "Inspection: passed\n"

    return ToolOutput(response_data={"traveler": traveler, "passed": True})
