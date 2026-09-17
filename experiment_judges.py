"""Provider adapters for blinded resume-comparison judges."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from experiment_config import JudgeConfig
from experiment_prompts import (
    ABSOLUTE_SYSTEM_PROMPT,
    PAIRWISE_SYSTEM_PROMPT,
    AbsoluteEvaluation,
    PairwiseComparison,
    PairwiseComparisonWire,
    build_absolute_prompt,
    build_pairwise_prompt,
)


class ExperimentJudgeError(RuntimeError):
    """Raised when a judge request fails or returns invalid data."""


@dataclass(frozen=True, slots=True)
class JudgeResponse:
    comparison: PairwiseComparison
    usage: dict[str, Any]
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AbsoluteJudgeResponse:
    evaluation: AbsoluteEvaluation
    usage: dict[str, Any]
    duration_seconds: float = 0.0


def judge_pair(
    *, judge: JudgeConfig, job_description: str, resume_x: str, resume_y: str
) -> JudgeResponse:
    """Dispatch one comparison to its configured provider."""

    prompt = build_pairwise_prompt(
        job_description=job_description,
        resume_x=resume_x,
        resume_y=resume_y,
    )
    if judge.provider == "openai":
        return _judge_with_openai(judge=judge, prompt=prompt)
    if judge.provider == "anthropic":
        return _judge_with_anthropic(judge=judge, prompt=prompt)
    raise ExperimentJudgeError(f"Unsupported judge provider: {judge.provider}")


def judge_resume(
    *, judge: JudgeConfig, job_description: str, resume: str
) -> AbsoluteJudgeResponse:
    """Score one resume independently of every other treatment output."""

    prompt = build_absolute_prompt(job_description=job_description, resume=resume)
    if judge.provider == "openai":
        return _score_with_openai(judge=judge, prompt=prompt)
    if judge.provider == "anthropic":
        return _score_with_anthropic(judge=judge, prompt=prompt)
    raise ExperimentJudgeError(f"Unsupported judge provider: {judge.provider}")


def check_model_access(judge: JudgeConfig) -> None:
    """Verify that the configured credential can retrieve a judge model."""

    load_dotenv()
    if judge.provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ExperimentJudgeError("OPENAI_API_KEY is not set.")
        OpenAI(api_key=api_key).models.retrieve(judge.model)
        return

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExperimentJudgeError("ANTHROPIC_API_KEY is not set.")
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - dependency installation path
        raise ExperimentJudgeError(
            "Anthropic judging requires the 'anthropic' package. Install project dependencies."
        ) from exc
    Anthropic(api_key=api_key).models.retrieve(judge.model)


def _judge_with_openai(*, judge: JudgeConfig, prompt: str) -> JudgeResponse:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ExperimentJudgeError("OPENAI_API_KEY is not set.")
    request: dict[str, Any] = {
        "model": judge.model,
        "input": [
            {"role": "system", "content": PAIRWISE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "resume_pairwise_comparison",
                "strict": True,
                "schema": PairwiseComparisonWire.model_json_schema(),
            }
        },
    }
    if judge.temperature is not None:
        request["temperature"] = judge.temperature
    if judge.reasoning_effort is not None:
        request["reasoning"] = {"effort": judge.reasoning_effort}
    try:
        started = perf_counter()
        response = OpenAI(api_key=api_key).responses.create(**request)
        duration = perf_counter() - started
        wire_comparison = PairwiseComparisonWire.model_validate_json(
            _extract_openai_text(response)
        )
        comparison = wire_comparison.to_comparison()
    except Exception as exc:  # pragma: no cover - SDK/network failures
        raise ExperimentJudgeError(
            f"OpenAI judge {judge.id!r} ({judge.model}) failed: {exc}"
        ) from exc
    return JudgeResponse(
        comparison=comparison,
        usage=_model_dump(response.usage),
        duration_seconds=round(duration, 3),
    )


def _judge_with_anthropic(*, judge: JudgeConfig, prompt: str) -> JudgeResponse:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExperimentJudgeError("ANTHROPIC_API_KEY is not set.")
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - dependency installation path
        raise ExperimentJudgeError(
            "Anthropic judging requires the 'anthropic' package. Install project dependencies."
        ) from exc
    try:
        request: dict[str, Any] = {
            "model": judge.model,
            "max_tokens": judge.max_output_tokens,
            "system": PAIRWISE_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "output_format": PairwiseComparisonWire,
        }
        if judge.anthropic_thinking is not None:
            request["thinking"] = {"type": judge.anthropic_thinking}
        if judge.reasoning_effort is not None:
            request["output_config"] = {"effort": judge.reasoning_effort}
        started = perf_counter()
        response = Anthropic(api_key=api_key).messages.parse(
            **request,
        )
        duration = perf_counter() - started
        wire_comparison = response.parsed_output
        if wire_comparison is None:
            raise ExperimentJudgeError("Anthropic returned no parsed comparison.")
        comparison = wire_comparison.to_comparison()
    except Exception as exc:  # pragma: no cover - SDK/network failures
        raise ExperimentJudgeError(
            f"Anthropic judge {judge.id!r} ({judge.model}) failed: {exc}"
        ) from exc
    return JudgeResponse(
        comparison=comparison,
        usage=_model_dump(response.usage),
        duration_seconds=round(duration, 3),
    )


def _score_with_openai(*, judge: JudgeConfig, prompt: str) -> AbsoluteJudgeResponse:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ExperimentJudgeError("OPENAI_API_KEY is not set.")
    request: dict[str, Any] = {
        "model": judge.model,
        "input": [
            {"role": "system", "content": ABSOLUTE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "resume_absolute_evaluation",
                "strict": True,
                "schema": AbsoluteEvaluation.model_json_schema(),
            }
        },
    }
    if judge.temperature is not None:
        request["temperature"] = judge.temperature
    if judge.reasoning_effort is not None:
        request["reasoning"] = {"effort": judge.reasoning_effort}
    try:
        started = perf_counter()
        response = OpenAI(api_key=api_key).responses.create(**request)
        duration = perf_counter() - started
        evaluation = AbsoluteEvaluation.model_validate_json(_extract_openai_text(response))
    except Exception as exc:  # pragma: no cover - SDK/network failures
        raise ExperimentJudgeError(
            f"OpenAI judge {judge.id!r} ({judge.model}) failed: {exc}"
        ) from exc
    return AbsoluteJudgeResponse(
        evaluation=evaluation,
        usage=_model_dump(response.usage),
        duration_seconds=round(duration, 3),
    )


def _score_with_anthropic(*, judge: JudgeConfig, prompt: str) -> AbsoluteJudgeResponse:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExperimentJudgeError("ANTHROPIC_API_KEY is not set.")
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover
        raise ExperimentJudgeError(
            "Anthropic judging requires the 'anthropic' package. Install project dependencies."
        ) from exc
    request: dict[str, Any] = {
        "model": judge.model,
        "max_tokens": judge.max_output_tokens,
        "system": ABSOLUTE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": AbsoluteEvaluation,
    }
    if judge.anthropic_thinking is not None:
        request["thinking"] = {"type": judge.anthropic_thinking}
    if judge.reasoning_effort is not None:
        request["output_config"] = {"effort": judge.reasoning_effort}
    try:
        started = perf_counter()
        response = Anthropic(api_key=api_key).messages.parse(**request)
        duration = perf_counter() - started
        evaluation = response.parsed_output
        if evaluation is None:
            raise ExperimentJudgeError("Anthropic returned no parsed absolute evaluation.")
    except Exception as exc:  # pragma: no cover - SDK/network failures
        raise ExperimentJudgeError(
            f"Anthropic judge {judge.id!r} ({judge.model}) failed: {exc}"
        ) from exc
    return AbsoluteJudgeResponse(
        evaluation=evaluation,
        usage=_model_dump(response.usage),
        duration_seconds=round(duration, 3),
    )


def _extract_openai_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) in {"output_text", "text"}:
                value = getattr(content, "text", None)
                if value:
                    return value
    raise ExperimentJudgeError("OpenAI judge returned no text output.")


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    try:
        return json.loads(json.dumps(value, default=lambda item: item.__dict__))
    except Exception:
        return {"value": str(value)}
