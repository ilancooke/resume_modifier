"""Anthropic client for evidence-based resume drafting and compression."""

from __future__ import annotations

import logging
import os
from time import perf_counter
from typing import TypeVar

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from generation_metrics import ModelCallMetrics, normalize_usage
from openai_client import (
    CompressionResult,
    ResumeTailoringError,
    TailoringTrace,
    _merge_compression,
    validate_and_normalize_tailoring,
)
from prompts import (
    COMPRESSION_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    FACT_CHECK_SYSTEM_PROMPT,
    build_compression_prompt,
    build_draft_prompt,
    build_fact_check_prompt,
)
from resume_models import TailoredResume


LOGGER = logging.getLogger(__name__)
ParsedModel = TypeVar("ParsedModel", bound=BaseModel)


def generate_tailored_resume_with_trace(
    *,
    career_history_context: str,
    allowed_sources_by_role: dict[str, set[str]],
    job_description: str,
    model: str,
    reasoning_effort: str | None = "medium",
    max_output_tokens: int = 16000,
    anthropic_thinking: str | None = "adaptive",
) -> TailoringTrace:
    """Draft and audit a resume with Claude while retaining usage measurements."""

    client = _create_client()
    calls: list[ModelCallMetrics] = []
    draft = _request_parsed(
        client=client,
        model=model,
        system_prompt=DRAFT_SYSTEM_PROMPT,
        user_prompt=build_draft_prompt(
            career_history_context=career_history_context,
            job_description=job_description,
        ),
        output_format=TailoredResume,
        request_name="resume draft",
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        anthropic_thinking=anthropic_thinking,
        calls=calls,
    )
    audited = _request_parsed(
        client=client,
        model=model,
        system_prompt=FACT_CHECK_SYSTEM_PROMPT,
        user_prompt=build_fact_check_prompt(
            career_history_context=career_history_context,
            job_description=job_description,
            draft=draft,
        ),
        output_format=TailoredResume,
        request_name="resume factuality review",
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        anthropic_thinking=anthropic_thinking,
        calls=calls,
    )
    tailoring = validate_and_normalize_tailoring(
        tailoring=audited,
        allowed_sources_by_role=allowed_sources_by_role,
    )
    return TailoringTrace(
        draft=draft,
        audited=audited,
        tailoring=tailoring,
        calls=tuple(calls),
    )


def compress_tailored_resume(
    *,
    tailoring: TailoredResume,
    overflow_text: str,
    overflow_word_count: int,
    attempt: int,
    model: str,
    reasoning_effort: str | None = "medium",
    max_output_tokens: int = 16000,
    anthropic_thinking: str | None = "adaptive",
    calls: list[ModelCallMetrics] | None = None,
) -> TailoredResume:
    """Shorten visible resume content with Claude while preserving evidence links."""

    compression = _request_parsed(
        client=_create_client(),
        model=model,
        system_prompt=COMPRESSION_SYSTEM_PROMPT,
        user_prompt=build_compression_prompt(
            tailoring=tailoring,
            overflow_text=overflow_text,
            overflow_word_count=overflow_word_count,
            attempt=attempt,
        ),
        output_format=CompressionResult,
        request_name="resume compression",
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        anthropic_thinking=anthropic_thinking,
        calls=calls,
    )
    return _merge_compression(tailoring=tailoring, compression=compression)


def _request_parsed(
    *,
    client: Anthropic,
    model: str,
    system_prompt: str,
    user_prompt: str,
    output_format: type[ParsedModel],
    request_name: str,
    reasoning_effort: str | None,
    max_output_tokens: int,
    anthropic_thinking: str | None,
    calls: list[ModelCallMetrics] | None,
) -> ParsedModel:
    LOGGER.info("Requesting %s from Anthropic model %s", request_name, model)
    request = {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "output_format": output_format,
    }
    if anthropic_thinking is not None:
        request["thinking"] = {"type": anthropic_thinking}
    if reasoning_effort is not None:
        request["output_config"] = {"effort": reasoning_effort}

    started = perf_counter()
    try:
        response = client.messages.parse(**request)
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(
            f"Anthropic {request_name} request failed: {exc}"
        ) from exc
    duration = perf_counter() - started
    parsed = response.parsed_output
    if parsed is None:
        raise ResumeTailoringError(
            f"Anthropic {request_name} returned no parsed structured output."
        )
    if calls is not None:
        input_tokens, cached_input_tokens, output_tokens = normalize_usage(response.usage)
        calls.append(
            ModelCallMetrics(
                stage=request_name,
                provider="anthropic",
                model=model,
                duration_seconds=round(duration, 3),
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        )
    return parsed


def _create_client() -> Anthropic:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ResumeTailoringError("ANTHROPIC_API_KEY is not set.")
    return Anthropic(api_key=api_key)

