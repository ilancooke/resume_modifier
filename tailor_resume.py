"""CLI entry point for evidence-based resume drafting and Word PDF export."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from career_history import CareerHistory, CareerHistoryError, load_career_history
from config import ConfigError, load_config
from docx_editor import (
    DocxEditingError,
    ResumeTemplate,
    build_diff_preview,
    extract_candidate_name,
    load_resume_template,
    write_tailored_resume,
)
from export_pdf import PdfLayoutError, export_docx_to_pdf, inspect_resume_pdf_layout
from openai_client import (
    ResumeTailoringError,
    compress_tailored_resume,
    generate_tailored_resume,
)
from resume_models import ROLE_SPECS, SUMMARY_TARGET_WORDS, TailoredResume, count_words
from validate_resume_match import format_report, validate_resume

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
            "Draft a tailored resume from Markdown career history, populate a DOCX template, "
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
        "--career-history",
        default=None,
        help="Path to the career-history Markdown folder. Defaults to career_history_dir in config.json.",
    )
    parser.add_argument(
        "--jd",
        default=None,
        help="Path to a text job description. Defaults to job_description_path in config.json.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.6-sol",
        help="OpenAI model used for drafting, factual review, compression, and match validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate content without writing DOCX or PDF files.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Print a unified placeholder-to-content preview.",
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

    try:
        config = load_config()
        template_path = (
            Path(args.resume).expanduser().resolve()
            if args.resume
            else config.resume_template_path
        )
        career_history_path = (
            Path(args.career_history).expanduser().resolve()
            if args.career_history
            else config.career_history_dir
        )
        job_description_path = (
            Path(args.jd).expanduser().resolve()
            if args.jd
            else config.job_description_path
        )
        job_description = job_description_path.read_text(encoding="utf-8").strip()
        if not job_description:
            raise ValueError("Job description file is empty.")

        template = load_resume_template(template_path)
        career_history = load_career_history(career_history_path)
        LOGGER.info(
            "Loaded %s career-history file(s); excluded: %s",
            len(career_history.files),
            ", ".join(career_history.excluded_files) or "none",
        )

        tailoring = request_tailoring(
            career_history=career_history,
            job_description=job_description,
            model=args.model,
        )
        metadata = enrich_role_metadata(
            metadata=RoleMetadata(
                candidate_slug=slugify(extract_candidate_name(template)),
                company_slug="unknown",
                role_number_slug="unknown",
            ),
            tailoring=tailoring,
        )
        docx_output_path, pdf_output_path = derive_output_paths(
            base_dir=config.tailored_resumes_dir,
            metadata=metadata,
        )

        print(format_content_report(tailoring))
        if args.show_diff or args.dry_run:
            print(build_diff_preview(template, tailoring))

        if args.dry_run:
            LOGGER.info("Dry run complete. No files were written.")
            return 0

        if docx_output_path == template.path:
            raise ValueError("Refusing to overwrite the source resume template.")

        write_tailored_resume(
            template=template,
            tailoring=tailoring,
            output_path=docx_output_path,
        )
        export_docx_to_pdf(docx_output_path, pdf_output_path)
        tailoring = repair_pdf_overflow(
            template=template,
            docx_output_path=docx_output_path,
            pdf_output_path=pdf_output_path,
            tailoring=tailoring,
            model=args.model,
        )
        print(
            run_tailored_match_validation(
                tailored_pdf_path=pdf_output_path,
                job_description=job_description,
                model=args.model,
            )
        )
    except (
        OSError,
        ValueError,
        ConfigError,
        CareerHistoryError,
        DocxEditingError,
        ResumeTailoringError,
        PdfLayoutError,
        RuntimeError,
    ) as exc:
        LOGGER.error("%s", exc)
        return 1

    print(f"DOCX: {docx_output_path}")
    print(f"PDF:  {pdf_output_path}")
    return 0


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
        tailoring = compress_tailored_resume(
            tailoring=tailoring,
            overflow_text=layout_check.overflow_text,
            overflow_word_count=layout_check.overflow_word_count,
            attempt=attempt,
            model=model,
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
) -> str:
    """Generate the tailored-only match report."""

    resume_text, report = validate_resume(
        resume_path=tailored_pdf_path,
        job_description=job_description,
        model=model,
    )
    return "Tailored Resume Match:\n" + format_report(report, resume_text)


def format_content_report(tailoring: TailoredResume) -> str:
    """Show word targets and evidence attribution for generated content."""

    lines = [
        "Generated resume content:",
        f"Summary: {count_words(tailoring.summary)} words (target ~{SUMMARY_TARGET_WORDS})",
        "Sources: " + ", ".join(tailoring.summary_source_files),
        "",
    ]
    for spec, role in zip(ROLE_SPECS, tailoring.roles, strict=True):
        role_words = sum(count_words(bullet.text) for bullet in role.bullets)
        lines.append(
            f"{spec.company} | {spec.role}: {len(role.bullets)} bullets, "
            f"{role_words} words (target ~{spec.target_words})"
        )
        for index, bullet in enumerate(role.bullets, start=1):
            lines.append(f"  {index}. {', '.join(bullet.source_files)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def derive_output_paths(*, base_dir: Path, metadata: RoleMetadata) -> tuple[Path, Path]:
    company_dir = base_dir / metadata.company_slug
    file_stem = f"{metadata.candidate_slug}_{metadata.company_slug}_{metadata.role_number_slug}"
    return company_dir / f"{file_stem}.docx", company_dir / f"{file_stem}.pdf"


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
