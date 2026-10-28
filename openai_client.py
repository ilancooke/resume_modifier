"""OpenAI client for evidence-based resume drafting and compression."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from generation_metrics import ModelCallMetrics, normalize_usage
from prompts import (
    COMPRESSION_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    FACT_CHECK_SYSTEM_PROMPT,
    FEEDBACK_REVISION_SYSTEM_PROMPT,
    build_compression_prompt,
    build_draft_prompt,
    build_fact_check_prompt,
    build_feedback_revision_prompt,
)
from resume_models import (
    EvidenceBullet,
    FeedbackRevision,
    FeedbackSignal,
    ROLE_SPECS,
    TailoredResume,
    TailoredRole,
)

LOGGER = logging.getLogger(__name__)


class ResumeTailoringError(RuntimeError):
    """Raised when a drafting request or structured response is invalid."""


@dataclass(frozen=True, slots=True)
class TailoringTrace:
    """Drafting artifacts retained for reproducible experiment runs."""

    draft: TailoredResume
    audited: TailoredResume
    tailoring: TailoredResume
    calls: tuple[ModelCallMetrics, ...] = ()


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
                "maxItems": spec.max_bullets,
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

FEEDBACK_REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tailoring": TAILORED_RESUME_SCHEMA,
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "signal_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["addressed", "already_demonstrated", "unsupported",
                                 "outside_editable_scope", "deferred"],
                    },
                    "rationale": {"type": "string"},
                    "source_files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["signal_id", "status", "rationale", "source_files"],
            },
        },
    },
    "required": ["tailoring", "decisions"],
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
                                "maxItems": spec.max_bullets,
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
    model: str = "gpt-6.1-terra",
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    max_output_tokens: int | None = None,
) -> TailoredResume:
    """Draft, fact-check, validate, and normalize tailored resume content."""

    return generate_tailored_resume_with_trace(
        career_history_context=career_history_context,
        allowed_sources_by_role=allowed_sources_by_role,
        job_description=job_description,
        model=model,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    ).tailoring


def generate_tailored_resume_with_trace(
    *,
    career_history_context: str,
    allowed_sources_by_role: dict[str, set[str]],
    job_description: str,
    model: str = "gpt-6.1-terra",
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    max_output_tokens: int | None = None,
) -> TailoringTrace:
    """Draft and audit a resume while retaining both model outputs."""

    client = _create_client()
    calls: list[ModelCallMetrics] = []
    draft = _request_tailored_resume(
        client=client,
        model=model,
        system_prompt=DRAFT_SYSTEM_PROMPT,
        user_prompt=build_draft_prompt(
            career_history_context=career_history_context,
            job_description=job_description,
        ),
        request_name="resume draft",
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        calls=calls,
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
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
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


def request_feedback_revision(
    *, career_history_context: str, allowed_sources_by_role: dict[str, set[str]],
    job_description: str, tailoring: TailoredResume, rendered_resume_text: str,
    match_report_json: str, signals: list[FeedbackSignal],
    proposal: FeedbackRevision | None = None, model: str = "gpt-6.1-terra",
    calls: list[ModelCallMetrics] | None = None,
) -> FeedbackRevision:
    """Propose or independently audit one feedback revision using all career evidence."""

    stage = "feedback factuality review" if proposal is not None else "feedback revision"
    system_prompt = FEEDBACK_REVISION_SYSTEM_PROMPT
    if proposal is not None:
        system_prompt += (
            "\nYou are the independent factuality reviewer. Verify every revised claim against "
            "the original evidence and correct both the resume and its decision explanations."
        )
    content = _request_json(
        client=_create_client(), model=model, system_prompt=system_prompt,
        user_prompt=build_feedback_revision_prompt(
            career_history_context=career_history_context, job_description=job_description,
            tailoring=tailoring, rendered_resume_text=rendered_resume_text,
            match_report_json=match_report_json, signals=signals, proposal=proposal,
        ),
        schema_name="feedback_revision", schema=FEEDBACK_REVISION_SCHEMA,
        request_name=stage, calls=calls,
    )
    try:
        revision = FeedbackRevision.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"{stage.capitalize()} validation failed: {exc}") from exc
    return validate_feedback_revision(
        revision=revision, original=tailoring, signals=signals,
        allowed_sources_by_role=allowed_sources_by_role,
    )


def validate_feedback_revision(
    *, revision: FeedbackRevision, original: TailoredResume,
    signals: list[FeedbackSignal], allowed_sources_by_role: dict[str, set[str]],
) -> FeedbackRevision:
    """Validate feedback coverage, evidence citations, and unchanged job identity."""

    if revision.tailoring.metadata != original.metadata:
        raise ResumeTailoringError("Feedback revision changed job metadata.")
    tailoring = validate_and_normalize_tailoring(
        tailoring=revision.tailoring, allowed_sources_by_role=allowed_sources_by_role,
    )
    if tailoring == original and any(decision.status == "addressed" for decision in revision.decisions):
        raise ResumeTailoringError("Unchanged resume cannot claim addressed feedback; use already_demonstrated.")
    ids = [decision.signal_id for decision in revision.decisions]
    if len(ids) != len(set(ids)) or set(ids) != {signal.signal_id for signal in signals}:
        raise ResumeTailoringError("Feedback decisions must cover every signal exactly once.")
    all_sources = set().union(*allowed_sources_by_role.values())
    cited_sources = set(tailoring.summary_source_files)
    cited_sources.update(source for role in tailoring.roles for bullet in role.bullets
                         for source in bullet.source_files)
    normalized = {}
    for decision in revision.decisions:
        if not decision.rationale.strip():
            raise ResumeTailoringError("Feedback decision rationale is empty.")
        sources, invalid = _normalize_source_files(decision.source_files, allowed_sources=all_sources)
        if invalid:
            raise ResumeTailoringError("Feedback decision cites unknown source(s): " + ", ".join(sorted(invalid)))
        if decision.status in {"addressed", "deferred"} and not sources:
            raise ResumeTailoringError(f"{decision.status} feedback decision requires evidence sources.")
        if decision.status == "addressed" and not set(sources).issubset(cited_sources):
            raise ResumeTailoringError("Addressed feedback evidence is not cited in the revised resume.")
        normalized[decision.signal_id] = decision.model_copy(update={"source_files": sources})
    return revision.model_copy(update={
        "tailoring": tailoring,
        "decisions": [normalized[signal.signal_id] for signal in signals],
    })


def compress_tailored_resume(
    *,
    tailoring: TailoredResume,
    overflow_text: str,
    overflow_word_count: int,
    attempt: int,
    model: str = "gpt-6.1-terra",
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    max_output_tokens: int | None = None,
    calls: list[ModelCallMetrics] | None = None,
    revision_objectives: str = "",
) -> TailoredResume:
    """Shorten visible content while preserving evidence and the selected bullet structure."""

    client = _create_client()
    prompt = build_compression_prompt(
        tailoring=tailoring,
        overflow_text=overflow_text,
        overflow_word_count=overflow_word_count,
        attempt=attempt,
        revision_objectives=revision_objectives,
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
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        calls=calls,
    )
    try:
        compression = CompressionResult.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResumeTailoringError(f"Compression output validation failed: {exc}") from exc
    return _merge_compression(tailoring=tailoring, compression=compression)


def validate_and_normalize_tailoring(
    *, tailoring: TailoredResume, allowed_sources_by_role: dict[str, set[str]]
) -> TailoredResume:
    """Enforce role structure, bullet maximums, and evidence attribution locally."""

    if not tailoring.summary.strip():
        raise ResumeTailoringError("Generated summary is empty.")
    if "—" in tailoring.summary:
        raise ResumeTailoringError("Generated summary contains a prohibited em dash.")

    all_sources = set().union(*allowed_sources_by_role.values())
    if not tailoring.summary_source_files:
        raise ResumeTailoringError("Generated summary does not cite any career-history source.")
    normalized_summary_sources, invalid_summary_sources = _normalize_source_files(
        tailoring.summary_source_files, allowed_sources=all_sources
    )
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
            LOGGER.warning(
                "Canonicalizing %s metadata from %r / %r to %r / %r",
                spec.role_id,
                role.company,
                role.role,
                spec.company,
                spec.role,
            )
        bullet_count = len(role.bullets)
        if not spec.valid_bullet_count(bullet_count):
            raise ResumeTailoringError(
                f"{spec.role_id} allows at most {spec.max_bullets} bullets "
                f"(preferred about {spec.preferred_bullets}), got {bullet_count}."
            )

        allowed_sources = allowed_sources_by_role.get(spec.role_id, set())
        normalized_bullets: list[EvidenceBullet] = []
        for bullet_index, bullet in enumerate(role.bullets, start=1):
            if not bullet.text.strip():
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} is empty."
                )
            if "—" in bullet.text:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} contains a prohibited em dash."
                )
            if not bullet.source_files:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} has no evidence source."
                )
            normalized_sources, invalid_sources = _normalize_source_files(
                bullet.source_files, allowed_sources=allowed_sources
            )
            if invalid_sources:
                raise ResumeTailoringError(
                    f"{spec.role_id} bullet {bullet_index} cites source(s) assigned to another role "
                    f"or not found: {', '.join(sorted(invalid_sources))}"
                )
            normalized_bullets.append(
                bullet.model_copy(update={"source_files": normalized_sources})
            )
        normalized_roles.append(
            role.model_copy(
                update={
                    "company": spec.company,
                    "role": spec.role,
                    "bullets": normalized_bullets,
                }
            )
        )

    return tailoring.model_copy(
        update={
            "summary_source_files": normalized_summary_sources,
            "roles": normalized_roles,
        }
    )


def _normalize_source_files(
    source_files: list[str], *, allowed_sources: set[str]
) -> tuple[list[str], set[str]]:
    """Canonicalize harmless path casing while rejecting unknown or ambiguous sources."""

    canonical_by_casefold: dict[str, str | None] = {}
    for source in allowed_sources:
        key = source.casefold()
        if key not in canonical_by_casefold:
            canonical_by_casefold[key] = source
        elif canonical_by_casefold[key] != source:
            canonical_by_casefold[key] = None

    normalized: list[str] = []
    invalid: set[str] = set()
    for generated_source in source_files:
        stripped_source = generated_source.strip()
        if stripped_source in allowed_sources:
            canonical_source = stripped_source
        else:
            canonical_source = canonical_by_casefold.get(stripped_source.casefold())
        if canonical_source is None:
            invalid.add(generated_source)
            continue
        if canonical_source not in normalized:
            normalized.append(canonical_source)
        if canonical_source != generated_source:
            LOGGER.debug(
                "Canonicalized generated source path %r to %r",
                generated_source,
                canonical_source,
            )
    return normalized, invalid


def _request_tailored_resume(
    *,
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    request_name: str,
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    max_output_tokens: int | None = None,
    calls: list[ModelCallMetrics] | None = None,
) -> TailoredResume:
    content = _request_json(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_name="tailored_resume",
        schema=TAILORED_RESUME_SCHEMA,
        request_name=request_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        calls=calls,
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
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    max_output_tokens: int | None = None,
    calls: list[ModelCallMetrics] | None = None,
) -> str:
    LOGGER.info("Requesting %s from OpenAI model %s", request_name, model)
    try:
        request: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if temperature is not None:
            request["temperature"] = temperature
        if reasoning_effort is not None:
            request["reasoning"] = {"effort": reasoning_effort}
        if max_output_tokens is not None:
            request["max_output_tokens"] = max_output_tokens
        started = perf_counter()
        response = client.responses.create(**request)
        duration = perf_counter() - started
    except Exception as exc:  # pragma: no cover - SDK/network failure path
        raise ResumeTailoringError(f"OpenAI {request_name} request failed: {exc}") from exc
    if calls is not None:
        input_tokens, cached_input_tokens, output_tokens = normalize_usage(response.usage)
        calls.append(
            ModelCallMetrics(
                stage=request_name,
                provider="openai",
                model=model,
                duration_seconds=round(duration, 3),
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        )
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
        source_bullet_count = len(source_role.bullets)
        if len(compressed_role.bullets) != source_bullet_count:
            raise ResumeTailoringError(
                f"Compression changed {spec.role_id} bullet count from {source_bullet_count} "
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
