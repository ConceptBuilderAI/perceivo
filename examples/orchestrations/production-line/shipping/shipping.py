import perceivo
from perceivo.primitives import EndInput, EndOutput


@perceivo.end
async def shipping(input: EndInput) -> EndOutput:
    """Ship the product, or hold it if it failed inspection."""
    passed = input.response_data.get("passed", False)

    return EndOutput(
        artifact=input.artifact,
        response_data={
            "product": input.response_data.get("product", ""),
            "stages_completed": input.response_data.get("stages_completed", 0),
            "status": "shipped" if passed else "held for rework",
        },
    )
