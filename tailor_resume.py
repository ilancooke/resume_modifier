"""Approved-bullet CLI and legacy helpers retained for narrative experiments."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from career_history import CareerHistory
from config import ConfigError, load_config
from docx_editor import (
    DocxEditingError,
    ResumeTemplate,
    build_diff_preview,
    write_tailored_resume,
)
from export_pdf import PdfLayoutError, export_docx_to_pdf, inspect_resume_pdf_layout
from feedback_revision import FeedbackOutcome, format_feedback_outcome
from match_client import ResumeMatchReport
from openai_client import (
    ResumeTailoringError,
    compress_tailored_resume,
    generate_tailored_resume,
)
from resume_models import ROLE_SPECS, SUMMARY_MAX_WORDS, TailoredResume, count_words
from resume_reader import ResumeText
from validate_resume_match import validate_resume

LOGGER = logging.getLogger(__name__)
MAX_LAYOUT_REPAIR_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class RoleMetadata:
    """Metadata used to route tailored resume outputs."""

    candidate_slug: str
    company_slug: str
    role_number_slug: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select approved resume bullets, populate a DOCX template, "
            "and export it to PDF with Microsoft Word."
        )
    )
    parser.add_argument(
        "--resume",
        "--template",
        dest="resume",
        default=None,
        help="Path to the resume stub DOCX. Defaults to resume_template_path in config.json.",
    )
    parser.add_argument(
        "--bullet-library",
        default=None,
        help="Approved-bullet folder. Defaults to bullet_library_dir in config.json.",
    )
    parser.add_argument(
        "--jd",
        default=None,
        help="Path to a text job description. Defaults to job_description_path in config.json.",
    )
    parser.add_argument(
        "--model",
        default="gpt-6.1-terra",
        help="OpenAI model used for Finbot relevance, selection, review, feedback, and matching.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decide Finbot, select and review approved bullets without files, PDF matching, or feedback revision.",
    )
    parser.add_argument(
        "--skip-feedback-revision",
        action="store_true",
        help="Keep the initial resume and match report without the additional feedback cycle.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Include a unified placeholder-to-content preview in the diagnostics file.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    # Experiments retain the legacy generation/compression functions below.
    from approved_workflow import run_approved_workflow

    try:
        return run_approved_workflow(args=args, config=load_config())
    except (OSError, ValueError, ConfigError, DocxEditingError,
            ResumeTailoringError, PdfLayoutError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 1


def request_tailoring(
    *, career_history: CareerHistory, job_description: str, model: str
) -> TailoredResume:
    return generate_tailored_resume(
        career_history_context=career_history.model_context(),
        allowed_sources_by_role=career_history.allowed_sources_by_role(),
        job_description=job_description,
        model=model,
    )


def repair_pdf_overflow(
    *,
    template: ResumeTemplate,
    docx_output_path: Path,
    pdf_output_path: Path,
    tailoring: TailoredResume,
    model: str,
    temperature: float | None = 0,
    reasoning_effort: str | None = "none",
    compressor: Callable[..., TailoredResume] | None = None,
) -> TailoredResume:
    """Enforce the hard two-page limit through conservative content compression."""

    for attempt in range(1, MAX_LAYOUT_REPAIR_ATTEMPTS + 1):
        layout_check = inspect_resume_pdf_layout(pdf_output_path)
        if not layout_check.has_overflow:
            return tailoring

        LOGGER.warning(
            "Resume has %s page(s) with %s extracted word(s) beyond page 2. "
            "Running compression attempt %s/%s.",
            layout_check.page_count,
            layout_check.overflow_word_count,
            attempt,
            MAX_LAYOUT_REPAIR_ATTEMPTS,
        )
        if compressor is None:
            tailoring = compress_tailored_resume(
                tailoring=tailoring,
                overflow_text=layout_check.overflow_text,
                overflow_word_count=layout_check.overflow_word_count,
                attempt=attempt,
                model=model,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        else:
            tailoring = compressor(
                tailoring=tailoring,
                overflow_text=layout_check.overflow_text,
                overflow_word_count=layout_check.overflow_word_count,
                attempt=attempt,
            )
        write_tailored_resume(
            template=template,
            tailoring=tailoring,
            output_path=docx_output_path,
        )
        export_docx_to_pdf(docx_output_path, pdf_output_path)

    layout_check = inspect_resume_pdf_layout(pdf_output_path)
    if layout_check.has_overflow:
        overflow_preview = layout_check.overflow_text or "[no extractable text]"
        raise PdfLayoutError(
            f"Could not fit the resume within 2 pages after "
            f"{MAX_LAYOUT_REPAIR_ATTEMPTS} compression attempts. The PDF still has "
            f"{layout_check.page_count} pages and {layout_check.overflow_word_count} extracted "
            f"word(s) beyond page 2.\n\nOverflow text:\n{overflow_preview}"
        )
    return tailoring


def run_tailored_match_validation(
    *, tailored_pdf_path: Path, job_description: str, model: str
) -> tuple[ResumeText, ResumeMatchReport]:
    """Keep the structured PDF evaluation available to the feedback cycle."""

    resume_text, report = validate_resume(
        resume_path=tailored_pdf_path,
        job_description=job_description,
        model=model,
    )
    return resume_text, report


def format_content_report(tailoring: TailoredResume) -> str:
    """Show content ceilings, selected bullet counts, and evidence attribution."""

    lines = [
        "Generated resume content:",
        f"Summary: {count_words(tailoring.summary)} words (max ~{SUMMARY_MAX_WORDS})",
        "Sources: " + ", ".join(tailoring.summary_source_files),
        "",
    ]
    for spec, role in zip(ROLE_SPECS, tailoring.roles, strict=True):
        role_words = sum(count_words(bullet.text) for bullet in role.bullets)
        lines.append(
            f"{spec.company} | {spec.role}: {len(role.bullets)} bullets, "
            f"{role_words} words (max ~{spec.max_words}; "
            f"preferred about {spec.preferred_bullets}, maximum {spec.max_bullets})"
        )
        for index, bullet in enumerate(role.bullets, start=1):
            lines.append(f"  {index}. {', '.join(bullet.source_files)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_diagnostics_report(
    *,
    tailoring: TailoredResume,
    template: ResumeTemplate,
    metadata: RoleMetadata,
    model: str,
    docx_output_path: Path,
    pdf_output_path: Path,
    match_report: str,
    include_diff: bool,
    feedback: FeedbackOutcome | None = None,
) -> str:
    """Combine generated-content evidence and match feedback for one resume run."""

    lines = [
        "Tailored Resume Diagnostics",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Model: {model}",
        f"Candidate: {metadata.candidate_slug}",
        f"Company: {metadata.company_slug}",
        f"Job ID: {metadata.role_number_slug}",
        f"DOCX: {docx_output_path}",
        f"PDF: {pdf_output_path}",
        "",
        format_content_report(tailoring),
    ]
    if include_diff:
        lines.extend(["", "Placeholder-to-content diff:", build_diff_preview(template, tailoring)])
    if feedback is not None:
        lines.extend(["", format_feedback_outcome(feedback)])
    lines.extend(["", match_report.strip()])
    return "\n".join(lines).rstrip() + "\n"


def write_diagnostics_file(*, output_path: Path, content: str) -> Path:
    """Atomically write a completed diagnostics report alongside generated resumes."""

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}_",
        suffix=".tmp",
        dir=output_path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return output_path


def derive_output_paths(*, base_dir: Path, metadata: RoleMetadata) -> tuple[Path, Path]:
    company_dir = base_dir / metadata.company_slug
    file_stem = f"{metadata.candidate_slug}_{metadata.company_slug}_{metadata.role_number_slug}"
    return company_dir / f"{file_stem}.docx", company_dir / f"{file_stem}.pdf"


def derive_diagnostics_path(*, base_dir: Path, metadata: RoleMetadata) -> Path:
    """Return the per-resume diagnostics file path."""

    file_stem = f"{metadata.candidate_slug}_{metadata.company_slug}_{metadata.role_number_slug}"
    return base_dir / f"{file_stem}.diagnostics.txt"


def enrich_role_metadata(
    *, metadata: RoleMetadata, tailoring: TailoredResume
) -> RoleMetadata:
    generated_company_slug = slugify(tailoring.metadata.company_name)
    company_slug = (
        generated_company_slug
        if generated_company_slug != "unknown"
        else metadata.company_slug
    )
    generated_job_id = slugify(tailoring.metadata.job_id)
    role_number_slug = (
        generated_job_id if generated_job_id != "unknown" else metadata.role_number_slug
    )
    return RoleMetadata(
        candidate_slug=metadata.candidate_slug,
        company_slug=company_slug or "unknown",
        role_number_slug=role_number_slug or "unknown",
    )


def slugify(value: str) -> str:
    ascii_value = (value or "").strip().lower()
    if not ascii_value:
        return "unknown"

    slug_chars: list[str] = []
    last_was_separator = False
    for char in ascii_value:
        if char.isascii() and char.isalnum():
            slug_chars.append(char)
            last_was_separator = False
        elif slug_chars and not last_was_separator:
            slug_chars.append("_")
            last_was_separator = True
    return "".join(slug_chars).strip("_") or "unknown"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
