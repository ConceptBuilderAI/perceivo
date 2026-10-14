import perceivo
from perceivo.primitives import StartInput, StartOutput


@perceivo.start
async def work_order(input: StartInput) -> StartOutput:
    """Check the work order and send it to the line supervisor."""
    product = input.request_data.get("product", "").strip()
    seconds = int(input.request_data.get("seconds_per_stage", 5))

    if not product:
        raise ValueError("'product' is required.")
    if not 1 <= seconds <= 30:
        raise ValueError("'seconds_per_stage' must be between 1 and 30.")

    return StartOutput(response_data={"product": product, "seconds_per_stage": seconds})
