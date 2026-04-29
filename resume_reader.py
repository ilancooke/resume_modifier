"""Read resume text from PDF files for match validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ResumeReadError(RuntimeError):
    """Raised when resume text cannot be extracted safely."""


@dataclass(frozen=True, slots=True)
class ResumeText:
    """Extracted resume text and extraction metadata."""

    path: Path
    text: str
    source_type: str
    page_count: int
    extraction_warnings: list[str]


def extract_resume_text(path: str | Path) -> ResumeText:
    """Extract text from a resume PDF using native PDF text when available."""

    resume_path = Path(path).expanduser().resolve()
    if not resume_path.exists():
        raise ResumeReadError(f"Resume file not found: {resume_path}")

    if resume_path.suffix.lower() != ".pdf":
        raise ResumeReadError(
            f"Resume validation currently expects a PDF file, got: {resume_path}"
        )

    return _extract_pdf_text(resume_path)


def _extract_pdf_text(path: Path) -> ResumeText:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - environment dependency path
        raise ResumeReadError(
            "Reading PDF resumes requires PyMuPDF. Install dependencies with: "
            "./.venv/bin/python -m pip install -e ."
        ) from exc

    try:
        document = pymupdf.open(path)
    except Exception as exc:  # pragma: no cover - library-specific parse failures
        raise ResumeReadError(f"Could not open PDF resume {path}: {exc}") from exc

    page_count = document.page_count
    page_texts: list[str] = []
    sparse_pages: list[int] = []
    try:
        for page_index, page in enumerate(document, start=1):
            page_text = page.get_text("text", sort=True).strip()
            if len(page_text) < 50:
                sparse_pages.append(page_index)
            if page_text:
                page_texts.append(page_text)
    finally:
        document.close()

    text = "\n\n".join(page_texts).strip()
    warnings = _build_extraction_warnings(
        text=text,
        page_count=page_count,
        sparse_pages=sparse_pages,
    )
    if not text:
        raise ResumeReadError(
            "No text could be extracted from the PDF. The resume may be scanned or image-only; OCR is not implemented yet."
        )

    return ResumeText(
        path=path,
        text=text,
        source_type="pdf_text",
        page_count=page_count,
        extraction_warnings=warnings,
    )


def _build_extraction_warnings(*, text: str, page_count: int, sparse_pages: list[int]) -> list[str]:
    warnings: list[str] = []
    word_count = len(text.split())

    if sparse_pages:
        page_list = ", ".join(str(page) for page in sparse_pages)
        warnings.append(
            f"Very little text was extracted from page(s) {page_list}; OCR may be needed for image-based content."
        )

    if word_count < 150:
        warnings.append(
            "The extracted resume text is unusually short; the score may be unreliable if the PDF uses images or complex layout."
        )

    if page_count == 0:
        warnings.append("The PDF did not appear to contain any readable pages.")

    return warnings
