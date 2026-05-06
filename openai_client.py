"""OpenAI client wrapper for structured resume tailoring output."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

from prompts import (
    BULLET_ORDER_SYSTEM_PROMPT,
    COMPRESSION_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    build_bullet_order_prompt,
    build_compression_prompt,
    build_rewrite_prompt,
)

LOGGER = logging.getLogger(__name__)


class ResumeTailoringError(RuntimeError):
    """Raised when the OpenAI API request or response is invalid."""


class TailoredExperience(BaseModel):
    """Tailored content for a single experience."""

    company: str
    role: str
    bullets: list[str]
    bullet_order: list[int]


class JobMetadata(BaseModel):
    """Structured job metadata used for naming tailored outputs."""

    company_name: str
    job_id: str


class RewrittenExperience(BaseModel):
    """Rewritten bullets for one experience, kept in original source order."""

    company: str
    role: str
    bullets: list[str]


class RewrittenResume(BaseModel):
    """Structured result from the rewrite-only model call."""

    summary: str
    experiences: list[RewrittenExperience]
    metadata: JobMetadata


class BulletOrderExperience(BaseModel):
    """Final bullet order for a single experience."""

    company: str
    role: str
    bullet_order: list[int]


class BulletOrderPlan(BaseModel):
    """Structured result from the order-only model call."""

    experiences: list[BulletOrderExperience]


class CompressionResult(BaseModel):
    """Compressed tailored content that preserves existing ordering metadata."""

    summary: str
    experiences: list[RewrittenExperience]


class TailoredResume(BaseModel):
    """Structured result returned by the model."""

    summary: str
    experiences: list[TailoredExperience]
    metadata: JobMetadata


REWRITTEN_RESUME_SCHEMA: dict[str, Any] = {
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
                },
                "required": ["company", "role", "bullets"],
            },
        },
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "company_name": {"type": "string"},
                "job_id": {"type": "string"},
            },
            "required": [
                "company_name",
                "job_id",
            ],
        },
    },
    "required": ["summary", "experiences", "metadata"],
}


BULLET_ORDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "experiences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "bullet_order": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["company", "role", "bullet_order"],
            },
        },
    },
    "required": ["experiences"],
}


COMPRESSION_SCHEMA: dict[str, Any] = {
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
                },
                "required": ["company", "role", "bullets"],
            },
        },
    },
    "required": ["summary", "experiences"],
}


def generate_tailored_resume(
    *,
    full_resume_text: str,
    editable_resume: dict[str, Any],
    job_description: str,
    model: str = "gpt-5.4-mini",
) -> TailoredResume:
    """Generate rewritten resume content and a separate bullet ordering plan."""

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ResumeTailoringError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    rewritten_resume = _generate_rewritten_resume(
        client=client,
        full_resume_text=full_resume_text,
        editable_resume=editable_resume,
        job_description=job_description,
        model=model,
    )
    bullet_order_plan = _generate_bullet_order_plan(
        client=client,
        editable_resume=editable_resume,
        rewritten_resume=rewritten_resume,
        job_description=job_description,
        model=model,
    )
    return _merge_tailoring(rewritten_resume=rewritten_resume, bullet_order_plan=bullet_order_plan)


def compress_tailored_resume(
    *,
    tailoring: TailoredResume,
    overflow_text: str,
    overflow_word_count: int,
    attempt: int,
    model: str = "gpt-5.4-mini",
) -> TailoredResume:
    """Compress tailored content enough to resolve a tiny PDF overflow."""

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ResumeTailoringError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    prompt = build_compression_prompt(
        tailored_resume=tailoring.model_dump(),
        overflow_text=overflow_text,
        overflow_word_count=overflow_word_count,
        attempt=attempt,
    )

    LOGGER.info("Requesting tiny-overflow compression from OpenAI model %s", model)
    try:
        response = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "resume_compression",
                    "strict": True,
                    "schema": COMPRESSION_SCHEMA,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI compression request failed: {exc}") from exc

    content = _extract_output_text(response)
    LOGGER.debug("Received compression payload: %s", content)

    try:
        payload = json.loads(content)
        compression = CompressionResult.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Compression output validation failed: {exc}") from exc

    return _merge_compression(tailoring=tailoring, compression=compression)


def _generate_rewritten_resume(
    *,
    client: OpenAI,
    full_resume_text: str,
    editable_resume: dict[str, Any],
    job_description: str,
    model: str,
) -> RewrittenResume:
    prompt = build_rewrite_prompt(
        full_resume_text=full_resume_text,
        editable_resume=editable_resume,
        job_description=job_description,
    )

    LOGGER.info("Requesting rewritten resume content from OpenAI model %s", model)
    try:
        response = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "resume_rewrite",
                    "strict": True,
                    "schema": REWRITTEN_RESUME_SCHEMA,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI rewrite request failed: {exc}") from exc

    content = _extract_output_text(response)
    LOGGER.debug("Received rewrite payload: %s", content)

    try:
        payload = json.loads(content)
        return RewrittenResume.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Rewrite output validation failed: {exc}") from exc


def _generate_bullet_order_plan(
    *,
    client: OpenAI,
    editable_resume: dict[str, Any],
    rewritten_resume: RewrittenResume,
    job_description: str,
    model: str,
) -> BulletOrderPlan:
    prompt = build_bullet_order_prompt(
        editable_resume=editable_resume,
        rewritten_resume=rewritten_resume.model_dump(),
        job_description=job_description,
    )

    LOGGER.info("Requesting bullet ordering plan from OpenAI model %s", model)
    try:
        response = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": BULLET_ORDER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "resume_bullet_order",
                    "strict": True,
                    "schema": BULLET_ORDER_SCHEMA,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI bullet ordering request failed: {exc}") from exc

    content = _extract_output_text(response)
    LOGGER.debug("Received bullet ordering payload: %s", content)

    try:
        payload = json.loads(content)
        return BulletOrderPlan.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Bullet ordering output validation failed: {exc}") from exc


def _merge_tailoring(
    *,
    rewritten_resume: RewrittenResume,
    bullet_order_plan: BulletOrderPlan,
) -> TailoredResume:
    if len(rewritten_resume.experiences) != len(bullet_order_plan.experiences):
        raise ResumeTailoringError(
            "Bullet ordering changed the number of experience sections, which is not allowed."
        )

    experiences: list[TailoredExperience] = []
    for index, (rewritten, ordered) in enumerate(
        zip(rewritten_resume.experiences, bullet_order_plan.experiences, strict=True)
    ):
        if rewritten.company != ordered.company:
            raise ResumeTailoringError(
                f"Bullet ordering experience {index} company mismatch: expected '{rewritten.company}', got '{ordered.company}'."
            )

        if rewritten.role != ordered.role:
            raise ResumeTailoringError(
                f"Bullet ordering experience {index} role mismatch: expected '{rewritten.role}', got '{ordered.role}'."
            )

        expected_order = set(range(len(rewritten.bullets)))
        generated_order = set(ordered.bullet_order)
        if generated_order != expected_order or len(ordered.bullet_order) != len(rewritten.bullets):
            raise ResumeTailoringError(
                f"Bullet ordering experience {index} must be a permutation of 0..{len(rewritten.bullets) - 1}."
            )

        experiences.append(
            TailoredExperience(
                company=rewritten.company,
                role=rewritten.role,
                bullets=rewritten.bullets,
                bullet_order=ordered.bullet_order,
            )
        )

    return TailoredResume(
        summary=rewritten_resume.summary,
        experiences=experiences,
        metadata=rewritten_resume.metadata,
    )


def _merge_compression(
    *,
    tailoring: TailoredResume,
    compression: CompressionResult,
) -> TailoredResume:
    if len(tailoring.experiences) != len(compression.experiences):
        raise ResumeTailoringError(
            "Compression changed the number of experience sections, which is not allowed."
        )

    experiences: list[TailoredExperience] = []
    for index, (source, compressed) in enumerate(
        zip(tailoring.experiences, compression.experiences, strict=True)
    ):
        if source.company != compressed.company:
            raise ResumeTailoringError(
                f"Compression experience {index} company mismatch: expected '{source.company}', got '{compressed.company}'."
            )

        if source.role != compressed.role:
            raise ResumeTailoringError(
                f"Compression experience {index} role mismatch: expected '{source.role}', got '{compressed.role}'."
            )

        if len(source.bullets) != len(compressed.bullets):
            raise ResumeTailoringError(
                f"Compression experience {index} changed bullet count from {len(source.bullets)} to {len(compressed.bullets)}."
            )

        experiences.append(
            TailoredExperience(
                company=source.company,
                role=source.role,
                bullets=compressed.bullets,
                bullet_order=source.bullet_order,
            )
        )

    return TailoredResume(
        summary=compression.summary,
        experiences=experiences,
        metadata=tailoring.metadata,
    )


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
