"""OpenAI client wrapper for structured resume match validation."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from match_prompts import MATCH_DIMENSIONS, SYSTEM_PROMPT, build_user_prompt

LOGGER = logging.getLogger(__name__)


class ResumeMatchError(RuntimeError):
    """Raised when the match validation request or response is invalid."""


class JobCriteriaSummary(BaseModel):
    """Criteria inferred from the job description."""

    role_family: str
    required_criteria: list[str]
    preferred_criteria: list[str]
    core_responsibilities: list[str]
    hard_blockers: list[str]


class DimensionScore(BaseModel):
    """Score and rationale for one match dimension."""

    name: str
    score: int
    max_score: int
    rationale: str
    evidence: list[str]
    missing_or_weak_signals: list[str]


class ResumeMatchReport(BaseModel):
    """Structured result returned by the model."""

    overall_score: int
    rating_label: str
    job_criteria_summary: JobCriteriaSummary
    dimension_scores: list[DimensionScore]
    strongest_matches: list[str]
    missing_or_weak_signals: list[str]
    knockout_risks: list[str]
    recommended_resume_changes: list[str]
    summary: str


DIMENSION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "enum": [name for name, _ in MATCH_DIMENSIONS]},
        "score": {"type": "integer"},
        "max_score": {"type": "integer"},
        "rationale": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "missing_or_weak_signals": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "name",
        "score",
        "max_score",
        "rationale",
        "evidence",
        "missing_or_weak_signals",
    ],
}


RESUME_MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overall_score": {"type": "integer"},
        "rating_label": {
            "type": "string",
            "enum": [
                "Excellent match",
                "Strong match",
                "Good match",
                "Partial match",
                "Weak match",
            ],
        },
        "job_criteria_summary": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "role_family": {"type": "string"},
                "required_criteria": {"type": "array", "items": {"type": "string"}},
                "preferred_criteria": {"type": "array", "items": {"type": "string"}},
                "core_responsibilities": {"type": "array", "items": {"type": "string"}},
                "hard_blockers": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "role_family",
                "required_criteria",
                "preferred_criteria",
                "core_responsibilities",
                "hard_blockers",
            ],
        },
        "dimension_scores": {
            "type": "array",
            "items": DIMENSION_SCHEMA,
        },
        "strongest_matches": {"type": "array", "items": {"type": "string"}},
        "missing_or_weak_signals": {"type": "array", "items": {"type": "string"}},
        "knockout_risks": {"type": "array", "items": {"type": "string"}},
        "recommended_resume_changes": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "overall_score",
        "rating_label",
        "job_criteria_summary",
        "dimension_scores",
        "strongest_matches",
        "missing_or_weak_signals",
        "knockout_risks",
        "recommended_resume_changes",
        "summary",
    ],
}


def generate_resume_match_report(
    *,
    resume_text: str,
    job_description: str,
    model: str = "gpt-5.4-mini",
) -> ResumeMatchReport:
    """Generate a structured resume-to-job match report from the OpenAI API."""

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ResumeMatchError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    prompt = build_user_prompt(
        resume_text=resume_text,
        job_description=job_description,
    )

    LOGGER.info("Requesting resume match validation from OpenAI model %s", model)
    try:
        response = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "resume_match_validation",
                    "strict": True,
                    "schema": RESUME_MATCH_SCHEMA,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeMatchError(f"OpenAI API request failed: {exc}") from exc

    content = _extract_output_text(response)
    LOGGER.debug("Received resume match payload: %s", content)

    try:
        payload = json.loads(content)
        report = ResumeMatchReport.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeMatchError(f"Structured output validation failed: {exc}") from exc

    _validate_report_dimensions(report)
    report.overall_score = _dimension_total(report)
    report.rating_label = _rating_label_for_score(report.overall_score)
    return report


def _validate_report_dimensions(report: ResumeMatchReport) -> None:
    expected_dimensions = dict(MATCH_DIMENSIONS)
    actual_dimensions = {dimension.name: dimension for dimension in report.dimension_scores}

    if set(actual_dimensions) != set(expected_dimensions):
        raise ResumeMatchError("Match report did not include exactly the expected scoring dimensions.")

    total_score = 0
    for name, max_score in expected_dimensions.items():
        dimension = actual_dimensions[name]
        if dimension.max_score != max_score:
            raise ResumeMatchError(
                f"Dimension '{name}' used max_score {dimension.max_score}; expected {max_score}."
            )
        if dimension.score < 0 or dimension.score > max_score:
            raise ResumeMatchError(
                f"Dimension '{name}' score {dimension.score} is outside 0..{max_score}."
            )
        total_score += dimension.score

    if total_score < 0 or total_score > 100:
        raise ResumeMatchError(f"Dimension total {total_score} is outside 0..100.")


def _dimension_total(report: ResumeMatchReport) -> int:
    return sum(dimension.score for dimension in report.dimension_scores)


def _rating_label_for_score(score: int) -> str:
    if score >= 90:
        return "Excellent match"
    if score >= 75:
        return "Strong match"
    if score >= 60:
        return "Good match"
    if score >= 40:
        return "Partial match"
    return "Weak match"


def _extract_output_text(response: Any) -> str:
    """Extract text content from a Responses API object."""

    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    output = getattr(response, "output", None) or []
    for item in output:
        for content in getattr(item, "content", None) or []:
            content_type = getattr(content, "type", None)
            if content_type == "refusal":
                refusal = getattr(content, "refusal", None) or "The model refused the request."
                raise ResumeMatchError(refusal)

            if content_type in {"output_text", "text"}:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    return text
                value = getattr(text, "value", None)
                if isinstance(value, str) and value:
                    return value

    raise ResumeMatchError("No structured text output was returned by the OpenAI API.")
