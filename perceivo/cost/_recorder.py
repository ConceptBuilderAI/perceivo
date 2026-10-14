"""Cost recording from within component execution.

Costs appear on orchestration runs and in cost reports. :func:`record` awaits
its write, so a caller that awaits it knows the cost was accepted before the
next statement runs.
"""

from typing import Any, Optional

from .._internal.context import _ExecutionContext
from ..types.records import ModelPricing, _from_wire


async def record(
    label: str,
    amount: float,
    currency: str = "USD",
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Record a cost entry against the current execution.

    Args:
        label: A descriptive label for the cost.
        amount: The cost amount in the specified currency.
        currency: Currency code (default: "USD").
        metadata: Optional key-value metadata to include with the cost entry.

    Example:
        ```python
        import perceivo

        await perceivo.cost.record("LLM call", 0.03, metadata={
            "provider": "openai",
            "model": "gpt-4o",
        })
        ```
    """
    ext = _ExecutionContext.get_ext()
    await ext.record_cost(label, amount, currency, metadata)


async def model_pricing(
    provider: str,
    model_name: str,
) -> Optional[ModelPricing]:
    """Look up LLM model pricing from the platform catalog.

    ``None`` means the model is not in the catalog. A ``0.0`` field means the
    model was found and that rate is either free or not recorded; the two cases
    are not distinguishable.

    Args:
        provider: The LLM provider (e.g., "anthropic", "openai", "google").
        model_name: The model identifier (e.g., "claude-sonnet-4-5-20250514").

    Returns:
        The model's :class:`~perceivo.types.records.ModelPricing`, or ``None``.

    Example:
        ```python
        import perceivo

        pricing = await perceivo.cost.model_pricing("anthropic", "claude-sonnet-4-5-20250514")
        if pricing:
            input_cost = pricing.input_price_per_1m_usd
            output_cost = pricing.output_price_per_1m_usd
        ```
    """
    ext = _ExecutionContext.get_ext()
    raw = await ext.get_llm_pricing(provider, model_name)
    return None if raw is None else _from_wire(ModelPricing, raw)
