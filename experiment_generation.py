"""Provider-neutral generation dispatch for resume experiments."""

from __future__ import annotations

from collections.abc import Callable

from experiment_config import ModelPricing, TreatmentConfig
from generation_metrics import ModelCallMetrics
from openai_client import TailoringTrace
from resume_models import TailoredResume


def generate_for_treatment(
    *,
    treatment: TreatmentConfig,
    career_history_context: str,
    allowed_sources_by_role: dict[str, set[str]],
    job_description: str,
) -> TailoringTrace:
    """Run the complete draft and factuality stages for one provider treatment."""

    if treatment.provider == "openai":
        from openai_client import generate_tailored_resume_with_trace

        return generate_tailored_resume_with_trace(
            career_history_context=career_history_context,
            allowed_sources_by_role=allowed_sources_by_role,
            job_description=job_description,
            model=treatment.model,
            temperature=treatment.temperature,
            reasoning_effort=treatment.reasoning_effort,
            max_output_tokens=treatment.max_output_tokens,
        )
    if treatment.provider == "anthropic":
        from anthropic_client import generate_tailored_resume_with_trace

        return generate_tailored_resume_with_trace(
            career_history_context=career_history_context,
            allowed_sources_by_role=allowed_sources_by_role,
            job_description=job_description,
            model=treatment.model,
            reasoning_effort=treatment.reasoning_effort,
            max_output_tokens=treatment.max_output_tokens,
            anthropic_thinking=treatment.anthropic_thinking or "adaptive",
        )
    raise ValueError(f"Unsupported generation provider: {treatment.provider}")


def compressor_for_treatment(
    *, treatment: TreatmentConfig, calls: list[ModelCallMetrics]
) -> Callable[..., TailoredResume]:
    """Return a layout-repair compressor bound to the treatment provider."""

    if treatment.provider == "openai":
        from openai_client import compress_tailored_resume

        def compress_openai(**kwargs) -> TailoredResume:
            return compress_tailored_resume(
                **kwargs,
                model=treatment.model,
                temperature=treatment.temperature,
                reasoning_effort=treatment.reasoning_effort,
                max_output_tokens=treatment.max_output_tokens,
                calls=calls,
            )

        return compress_openai

    if treatment.provider == "anthropic":
        from anthropic_client import compress_tailored_resume

        def compress_anthropic(**kwargs) -> TailoredResume:
            return compress_tailored_resume(
                **kwargs,
                model=treatment.model,
                reasoning_effort=treatment.reasoning_effort,
                max_output_tokens=treatment.max_output_tokens,
                anthropic_thinking=treatment.anthropic_thinking or "adaptive",
                calls=calls,
            )

        return compress_anthropic

    raise ValueError(f"Unsupported generation provider: {treatment.provider}")


def estimate_call_cost(
    *, calls: list[ModelCallMetrics] | tuple[ModelCallMetrics, ...], pricing: ModelPricing | None
) -> float | None:
    """Estimate USD cost from normalized token usage and configured list prices."""

    if pricing is None:
        return None
    cached_rate = (
        pricing.cached_input_per_million
        if pricing.cached_input_per_million is not None
        else pricing.input_per_million
    )
    cost = 0.0
    for call in calls:
        cost += call.input_tokens * pricing.input_per_million / 1_000_000
        cost += call.cached_input_tokens * cached_rate / 1_000_000
        cost += call.output_tokens * pricing.output_per_million / 1_000_000
    return round(cost, 6)
