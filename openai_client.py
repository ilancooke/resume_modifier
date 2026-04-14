"""OpenAI client wrapper for structured resume tailoring output."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT, build_user_prompt

LOGGER = logging.getLogger(__name__)


class ResumeTailoringError(RuntimeError):
    """Raised when the OpenAI API request or response is invalid."""


class TailoredExperience(BaseModel):
    """Tailored content for a single experience."""

    company: str
    role: str
    bullets: list[str]
    bullet_order: list[int]


class TrackerDetails(BaseModel):
    """Structured job application tracker metadata."""

    company_name: str
    role_title: str
    location: str
    job_id: str
    ds_role_type: str
    alignment_strength_comment: str
    salary_range: str


class TailoredResume(BaseModel):
    """Structured result returned by the model."""

    summary: str
    experiences: list[TailoredExperience]
    tracker: TrackerDetails


RESUME_TAILORING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "experiences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "bullets": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "bullet_order": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["company", "role", "bullets", "bullet_order"],
            },
        },
        "tracker": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "company_name": {"type": "string"},
                "role_title": {"type": "string"},
                "location": {"type": "string"},
                "job_id": {"type": "string"},
                "ds_role_type": {
                    "type": "string",
                    "enum": [
                        "Product / Experimentation DS",
                        "Applied ML",
                        "Machine Learning/Predictive model building",
                        "ML Engineering",
                        "AI / LLM / Agentic",
                        "Analytics / BI",
                        "Other",
                    ],
                },
                "alignment_strength_comment": {"type": "string"},
                "salary_range": {"type": "string"},
            },
            "required": [
                "company_name",
                "role_title",
                "location",
                "job_id",
                "ds_role_type",
                "alignment_strength_comment",
                "salary_range",
            ],
        },
    },
    "required": ["summary", "experiences", "tracker"],
}


def generate_tailored_resume(
    *,
    full_resume_text: str,
    editable_resume: dict[str, Any],
    job_description: str,
    model: str = "gpt-5.4-mini",
) -> TailoredResume:
    """Generate a structured tailoring plan from the OpenAI API."""

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ResumeTailoringError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    prompt = build_user_prompt(
        full_resume_text=full_resume_text,
        editable_resume=editable_resume,
        job_description=job_description,
    )

    LOGGER.info("Requesting tailored resume content from OpenAI model %s", model)
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
                    "name": "resume_tailoring",
                    "strict": True,
                    "schema": RESUME_TAILORING_SCHEMA,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI API request failed: {exc}") from exc

    content = _extract_output_text(response)
    LOGGER.debug("Received structured output payload: %s", content)

    try:
        payload = json.loads(content)
        return TailoredResume.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Structured output validation failed: {exc}") from exc


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
                raise ResumeTailoringError(refusal)

            if content_type in {"output_text", "text"}:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text:
                    return text
                value = getattr(text, "value", None)
                if isinstance(value, str) and value:
                    return value

    raise ResumeTailoringError("No structured text output was returned by the OpenAI API.")
