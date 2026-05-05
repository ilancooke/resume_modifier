"""CLI entry point for tailoring a resume and exporting it to PDF."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from config import ConfigError, load_config
from docx_editor import apply_tailoring, build_diff_preview, load_resume_structure, save_resume
from export_pdf import export_docx_to_pdf
from openai_client import ResumeTailoringError, TailoredResume, generate_tailored_resume

LOGGER = logging.getLogger(__name__)


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
        help="OpenAI model to use for the structured tailoring call.",
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
    except (OSError, ValueError, ConfigError, ResumeTailoringError, RuntimeError) as exc:
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
