"""OpenAI client for evidence-based resume drafting and compression."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from prompts import (
    COMPRESSION_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    FACT_CHECK_SYSTEM_PROMPT,
    build_compression_prompt,
    build_draft_prompt,
    build_fact_check_prompt,
)
from resume_models import (
    EvidenceBullet,
    ROLE_SPECS,
    TailoredResume,
    TailoredRole,
)

LOGGER = logging.getLogger(__name__)


class ResumeTailoringError(RuntimeError):
    """Raised when a drafting request or structured response is invalid."""


class CompressedRole(BaseModel):
    """Shortened bullet text for one role."""

    role_id: str
    bullets: list[str]


class CompressionResult(BaseModel):
    """Shortened visible content with unchanged resume structure."""

    summary: str
    roles: list[CompressedRole]


EVIDENCE_BULLET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "source_files": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "source_files"],
}


def _tailored_role_schema(spec) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "role_id": {"type": "string", "enum": [spec.role_id]},
            "company": {"type": "string", "enum": [spec.company]},
            "role": {"type": "string", "enum": [spec.role]},
            "bullets": {
                "type": "array",
                "items": EVIDENCE_BULLET_SCHEMA,
                "minItems": spec.bullet_count,
                "maxItems": spec.bullet_count,
            },
        },
        "required": ["role_id", "company", "role", "bullets"],
    }


TAILORED_RESUME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "summary_source_files": {"type": "array", "items": {"type": "string"}},
        "roles": {
            "type": "array",
            "items": {"anyOf": [_tailored_role_schema(spec) for spec in ROLE_SPECS]},
            "minItems": len(ROLE_SPECS),
            "maxItems": len(ROLE_SPECS),
        },
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "company_name": {"type": "string"},
                "job_id": {"type": "string"},
            },
            "required": ["company_name", "job_id"],
        },
    },
    "required": ["summary", "summary_source_files", "roles", "metadata"],
}

COMPRESSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "roles": {
            "type": "array",
            "items": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "role_id": {"type": "string", "enum": [spec.role_id]},
                            "bullets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": spec.bullet_count,
                                "maxItems": spec.bullet_count,
                            },
                        },
                        "required": ["role_id", "bullets"],
                    }
                    for spec in ROLE_SPECS
                ]
            },
            "minItems": len(ROLE_SPECS),
            "maxItems": len(ROLE_SPECS),
        },
    },
    "required": ["summary", "roles"],
}


def generate_tailored_resume(
    *,
    career_history_context: str,
    allowed_sources_by_role: dict[str, set[str]],
    job_description: str,
    model: str = "gpt-5.4-mini",
) -> TailoredResume:
    """Draft, fact-check, validate, and normalize tailored resume content."""

    client = _create_client()
    draft = _request_tailored_resume(
        client=client,
        model=model,
        system_prompt=DRAFT_SYSTEM_PROMPT,
        user_prompt=build_draft_prompt(
            career_history_context=career_history_context,
            job_description=job_description,
        ),
        request_name="resume draft",
    )

    LOGGER.info("Requesting independent resume factuality review from OpenAI model %s", model)
    audited = _request_tailored_resume(
        client=client,
        model=model,
        system_prompt=FACT_CHECK_SYSTEM_PROMPT,
        user_prompt=build_fact_check_prompt(
            career_history_context=career_history_context,
            job_description=job_description,
            draft=draft,
        ),
        request_name="resume factuality review",
    )
    return validate_and_normalize_tailoring(
        tailoring=audited,
        allowed_sources_by_role=allowed_sources_by_role,
    )


def compress_tailored_resume(
    *,
    tailoring: TailoredResume,
    overflow_text: str,
    overflow_word_count: int,
    attempt: int,
    model: str = "gpt-5.4-mini",
) -> TailoredResume:
    """Shorten visible content while preserving evidence and exact bullet structure."""

    client = _create_client()
    prompt = build_compression_prompt(
        tailoring=tailoring,
        overflow_text=overflow_text,
        overflow_word_count=overflow_word_count,
        attempt=attempt,
    )
    LOGGER.info("Requesting two-page compression from OpenAI model %s", model)
    content = _request_json(
        client=client,
        model=model,
        system_prompt=COMPRESSION_SYSTEM_PROMPT,
        user_prompt=prompt,
        schema_name="resume_compression",
        schema=COMPRESSION_SCHEMA,
        request_name="resume compression",
    )
    try:
        compression = CompressionResult.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Compression output validation failed: {exc}") from exc
    return _merge_compression(tailoring=tailoring, compression=compression)


def validate_and_normalize_tailoring(
    *, tailoring: TailoredResume, allowed_sources_by_role: dict[str, set[str]]
) -> TailoredResume:
    """Enforce exact role structure and evidence attribution locally."""

    if not tailoring.summary.strip():
        raise ResumeTailoringError("Generated summary is empty.")
    if "—" in tailoring.summary:
        raise ResumeTailoringError("Generated summary contains a prohibited em dash.")

    all_sources = set().union(*allowed_sources_by_role.values())
    summary_sources = set(tailoring.summary_source_files)
    if not summary_sources:
        raise ResumeTailoringError("Generated summary does not cite any career-history source.")
    invalid_summary_sources = summary_sources - all_sources
    if invalid_summary_sources:
        raise ResumeTailoringError(
            "Generated summary cites unknown source file(s): "
            + ", ".join(sorted(invalid_summary_sources))
        )

    roles_by_id: dict[str, TailoredRole] = {}
    for role in tailoring.roles:
        if role.role_id in roles_by_id:
            raise ResumeTailoringError(f"Generated duplicate role: {role.role_id}")
        roles_by_id[role.role_id] = role

    expected_ids = {spec.role_id for spec in ROLE_SPECS}
    if set(roles_by_id) != expected_ids:
        missing = sorted(expected_ids - set(roles_by_id))
        extra = sorted(set(roles_by_id) - expected_ids)
        raise ResumeTailoringError(
            f"Generated role set is invalid. Missing: {missing or 'none'}; extra: {extra or 'none'}."
        )

    normalized_roles: list[TailoredRole] = []
    for spec in ROLE_SPECS:
        role = roles_by_id[spec.role_id]
        if role.company != spec.company or role.role != spec.role:
            raise ResumeTailoringError(
                f"{spec.role_id} metadata mismatch; expected {spec.company!r} / {spec.role!r}."
            )
        if len(role.bullets) != spec.bullet_count:
            raise ResumeTailoringError(
                f"{spec.role_id} requires exactly {spec.bullet_count} bullets, "
                f"got {len(role.bullets)}."
            )

        allowed_sources = allowed_sources_by_role.get(spec.role_id, set())
        for bullet_index, bullet in enumerate(role.bullets, start=1):
            if not bullet.text.strip():
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} is empty."
                )
            if "—" in bullet.text:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} contains a prohibited em dash."
                )
            cited_sources = set(bullet.source_files)
            if not cited_sources:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} has no evidence source."
                )
            invalid_sources = cited_sources - allowed_sources
            if invalid_sources:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} cites source(s) assigned to another role "
                    f"or not found: {', '.join(sorted(invalid_sources))}"
                )
        normalized_roles.append(role)

    return tailoring.model_copy(update={"roles": normalized_roles})


def _request_tailored_resume(
    *,
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    request_name: str,
) -> TailoredResume:
    content = _request_json(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_name="tailored_resume",
        schema=TAILORED_RESUME_SCHEMA,
        request_name=request_name,
    )
    try:
        return TailoredResume.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"{request_name.capitalize()} validation failed: {exc}") from exc


def _request_json(
    *,
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    request_name: str,
) -> str:
    LOGGER.info("Requesting %s from OpenAI model %s", request_name, model)
    try:
        response = client.responses.create(
            model=model,
            temperature=0,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        )
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI {request_name} request failed: {exc}") from exc
    content = _extract_output_text(response)
    LOGGER.debug("Received %s payload: %s", request_name, content)
    return content


def _create_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ResumeTailoringError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=api_key)


def _merge_compression(
    *, tailoring: TailoredResume, compression: CompressionResult
) -> TailoredResume:
    compressed_by_id = {role.role_id: role for role in compression.roles}
    if len(compressed_by_id) != len(compression.roles):
        raise ResumeTailoringError("Compression returned duplicate role IDs.")

    merged_roles: list[TailoredRole] = []
    for spec, source_role in zip(ROLE_SPECS, tailoring.roles, strict=True):
        compressed_role = compressed_by_id.get(spec.role_id)
        if compressed_role is None:
            raise ResumeTailoringError(f"Compression omitted role: {spec.role_id}")
        if len(compressed_role.bullets) != spec.bullet_count:
            raise ResumeTailoringError(
                f"Compression changed {spec.role_id} bullet count from {spec.bullet_count} "
                f"to {len(compressed_role.bullets)}."
            )

        bullets = []
        for index, (source_bullet, compressed_text) in enumerate(
            zip(source_role.bullets, compressed_role.bullets, strict=True), start=1
        ):
            text = compressed_text.strip()
            if not text:
                raise ResumeTailoringError(
                    f"Compression emptied {spec.role_id} bullet {index}."
                )
            if "—" in text:
                raise ResumeTailoringError(
                    f"Compression introduced an em dash in {spec.role_id} bullet {index}."
                )
            bullets.append(
                EvidenceBullet(text=text, source_files=source_bullet.source_files)
            )
        merged_roles.append(source_role.model_copy(update={"bullets": bullets}))

    summary = compression.summary.strip()
    if not summary:
        raise ResumeTailoringError("Compression emptied the summary.")
    if "—" in summary:
        raise ResumeTailoringError("Compression introduced an em dash in the summary.")

    return tailoring.model_copy(update={"summary": summary, "roles": merged_roles})


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) in {"output_text", "text"}:
                text = getattr(content, "text", None)
                if text:
                    return text
    raise ResumeTailoringError("OpenAI response did not contain text output.")
