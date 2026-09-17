"""Normalized usage and latency records for model-backed resume generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelCallMetrics:
    """Provider-neutral measurements for one API request."""

    stage: str
    provider: str
    model: str
    duration_seconds: float
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def normalize_usage(usage: Any) -> tuple[int, int, int]:
    """Return input, cached-input, and output tokens from either provider SDK."""

    if usage is None:
        return 0, 0, 0
    if hasattr(usage, "model_dump"):
        payload = usage.model_dump()
    elif isinstance(usage, dict):
        payload = usage
    else:
        payload = getattr(usage, "__dict__", {})

    input_tokens = int(payload.get("input_tokens", 0) or 0)
    output_tokens = int(payload.get("output_tokens", 0) or 0)
    cached_input_tokens = int(payload.get("cache_read_input_tokens", 0) or 0)
    input_details = payload.get("input_tokens_details") or {}
    if hasattr(input_details, "model_dump"):
        input_details = input_details.model_dump()
    if isinstance(input_details, dict):
        openai_cached = int(input_details.get("cached_tokens", 0) or 0)
        cached_input_tokens += openai_cached
        input_tokens = max(0, input_tokens - openai_cached)
    return input_tokens, cached_input_tokens, output_tokens
