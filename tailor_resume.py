"""CLI entry point for tailoring a resume and exporting it to PDF."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from config import ConfigError, load_config
from docx_editor import apply_tailoring, build_diff_preview, load_resume_structure, save_resume
from export_pdf import (
    TINY_OVERFLOW_WORD_LIMIT,
    PdfLayoutError,
    export_docx_to_pdf,
    inspect_resume_pdf_layout,
)
from openai_client import (
    ResumeTailoringError,
    TailoredResume,
    compress_tailored_resume,
    generate_tailored_resume,
)
from validate_resume_match import format_dimension_comparison_table, validate_resume

LOGGER = logging.getLogger(__name__)
MAX_LAYOUT_REPAIR_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class RoleMetadata:
    """Metadata used to route tailored resume outputs."""

    candidate_slug: str
    company_slug: str
    role_number_slug: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tailor a DOCX resume to a job description and export the result to PDF with Apple Pages."
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Path to the base resume DOCX file. Defaults to the value in config.json.",
    )
    parser.add_argument(
        "--jd",
        default=None,
        help="Path to a text file containing the job description. Defaults to the value in config.json.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4-mini",
        help="OpenAI model to use for structured tailoring and match validation calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate the tailoring output without writing files.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Print a unified diff preview of the summary and bullet updates.",
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
        resume_path = Path(args.resume).expanduser().resolve() if args.resume else config.base_resume_path
        job_description_path = (
            Path(args.jd).expanduser().resolve() if args.jd else config.job_description_path
        )
        job_description = job_description_path.read_text(encoding="utf-8").strip()
        if not job_description:
            raise ValueError("Job description file is empty.")

        structure = load_resume_structure(resume_path)
        metadata = extract_role_metadata(structure=structure, job_description=job_description)
        tailoring = request_tailoring(
            structure=structure,
            job_description=job_description,
            model=args.model,
        )
        metadata = enrich_role_metadata(metadata=metadata, tailoring=tailoring)
        docx_output_path, pdf_output_path = derive_output_paths(
            base_dir=config.tailored_resumes_dir,
            metadata=metadata,
        )
        print(format_bullet_order_report(tailoring))

        if args.show_diff or args.dry_run:
            diff_preview = build_diff_preview(structure, tailoring)
            if diff_preview:
                print(diff_preview)
            else:
                print("No content changes detected.")

        if args.dry_run:
            LOGGER.info("Dry run complete. No files were written.")
            return 0

        if docx_output_path == resume_path:
            raise ValueError(
                "Refusing to overwrite the source resume. Use a different --out-prefix."
            )

        docx_output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resume_path, docx_output_path)
        LOGGER.info("Copied base resume to %s", docx_output_path)

        editable_structure = load_resume_structure(docx_output_path)
        apply_tailoring(editable_structure, tailoring)
        save_resume(editable_structure, docx_output_path)
        export_docx_to_pdf(docx_output_path, pdf_output_path)
        tailoring = repair_tiny_pdf_overflow(
            docx_output_path=docx_output_path,
            pdf_output_path=pdf_output_path,
            tailoring=tailoring,
            model=args.model,
        )
        print(
            run_match_comparison(
                base_resume_path=resume_path,
                tailored_pdf_path=pdf_output_path,
                job_description=job_description,
                model=args.model,
            )
        )
    except (OSError, ValueError, ConfigError, ResumeTailoringError, PdfLayoutError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 1

    print(f"DOCX: {docx_output_path}")
    print(f"PDF:  {pdf_output_path}")
    return 0


def request_tailoring(*, structure, job_description: str, model: str) -> TailoredResume:
    return generate_tailored_resume(
        full_resume_text=structure.full_text(),
        editable_resume=structure.editable_payload(),
        job_description=job_description,
        model=model,
    )


def repair_tiny_pdf_overflow(
    *,
    docx_output_path: Path,
    pdf_output_path: Path,
    tailoring: TailoredResume,
    model: str,
) -> TailoredResume:
    """Repair tiny rendered overflow by compressing content and re-exporting."""

    for attempt in range(1, MAX_LAYOUT_REPAIR_ATTEMPTS + 1):
        layout_check = inspect_resume_pdf_layout(pdf_output_path)
        if not layout_check.has_overflow:
            return tailoring

        if layout_check.overflow_word_count > TINY_OVERFLOW_WORD_LIMIT:
            LOGGER.warning(
                "Exported resume has %s pages; page %s contains %s words.",
                layout_check.page_count,
                layout_check.overflow_page_number,
                layout_check.overflow_word_count,
            )
            return tailoring

        LOGGER.warning(
            "Tailored PDF spilled onto page %s with %s word(s). Running compression attempt %s/%s.",
            layout_check.overflow_page_number,
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
        editable_structure = load_resume_structure(docx_output_path)
        apply_tailoring(editable_structure, tailoring)
        save_resume(editable_structure, docx_output_path)
        export_docx_to_pdf(docx_output_path, pdf_output_path)

    layout_check = inspect_resume_pdf_layout(pdf_output_path)
    if layout_check.has_overflow and layout_check.overflow_word_count <= TINY_OVERFLOW_WORD_LIMIT:
        overflow_preview = layout_check.overflow_text or "[no extractable text]"
        raise PdfLayoutError(
            f"Could not resolve tiny page overflow after {MAX_LAYOUT_REPAIR_ATTEMPTS} compression attempt(s). "
            f"Page {layout_check.overflow_page_number} still has {layout_check.overflow_word_count} word(s).\n\n"
            f"Page {layout_check.overflow_page_number} text:\n{overflow_preview}"
        )

    return tailoring


def run_match_comparison(
    *,
    base_resume_path: Path,
    tailored_pdf_path: Path,
    job_description: str,
    model: str,
) -> str:
    """Validate base and tailored resume match scores and format their score movement."""

    with TemporaryDirectory(prefix="resume_match_") as temp_dir:
        base_pdf_path = Path(temp_dir) / f"{base_resume_path.stem}.pdf"
        export_docx_to_pdf(base_resume_path, base_pdf_path)
        _, base_report = validate_resume(
            resume_path=base_pdf_path,
            job_description=job_description,
            model=model,
        )
        _, tailored_report = validate_resume(
            resume_path=tailored_pdf_path,
            job_description=job_description,
            model=model,
        )

    return format_dimension_comparison_table(
        base_report=base_report,
        tailored_report=tailored_report,
    )


def format_bullet_order_report(tailoring: TailoredResume) -> str:
    """Format the generated bullet order for terminal visibility."""

    lines = ["Bullet order:"]
    for experience in tailoring.experiences:
        display_order = [index + 1 for index in experience.bullet_order]
        lines.append(f"{experience.company} | {experience.role}")
        lines.append(f"bullet_order: {display_order}")
        lines.append("")

    return "\n".join(lines).rstrip()


def derive_output_paths(*, base_dir: Path, metadata: RoleMetadata) -> tuple[Path, Path]:
    company_dir = base_dir / metadata.company_slug
    file_stem = f"{metadata.candidate_slug}_{metadata.company_slug}_{metadata.role_number_slug}"
    return company_dir / f"{file_stem}.docx", company_dir / f"{file_stem}.pdf"


def extract_role_metadata(*, structure, job_description: str) -> RoleMetadata:
    candidate_slug = extract_candidate_slug(structure)
    return RoleMetadata(
        candidate_slug=candidate_slug,
        company_slug="unknown",
        role_number_slug="unknown",
    )


def enrich_role_metadata(*, metadata: RoleMetadata, tailoring: TailoredResume) -> RoleMetadata:
    generated_company_slug = slugify(tailoring.metadata.company_name)
    company_slug = generated_company_slug if generated_company_slug != "unknown" else metadata.company_slug

    role_number_slug = metadata.role_number_slug
    if role_number_slug == "unknown":
        role_number_slug = slugify(tailoring.metadata.job_id)

    return RoleMetadata(
        candidate_slug=metadata.candidate_slug,
        company_slug=company_slug or "unknown",
        role_number_slug=role_number_slug or "unknown",
    )


def extract_candidate_slug(structure) -> str:
    for paragraph in structure.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if text.upper() == text:
            slug = slugify(text)
            if slug and slug != "unknown":
                return slug
    return slugify(structure.path.stem)


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
            continue

        if slug_chars and not last_was_separator:
            slug_chars.append("_")
            last_was_separator = True

    slug = "".join(slug_chars).strip("_")
    return slug or "unknown"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
