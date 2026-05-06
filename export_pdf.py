"""Apple Pages PDF export utilities for macOS."""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class PdfExportError(RuntimeError):
    """Raised when the Pages export step fails."""


class PdfLayoutError(RuntimeError):
    """Raised when an exported PDF fails layout constraints."""


MAX_RESUME_PAGES = 2
TINY_OVERFLOW_WORD_LIMIT = 50


@dataclass(frozen=True, slots=True)
class PdfLayoutCheck:
    """Rendered PDF layout details for resume overflow handling."""

    pdf_path: Path
    page_count: int
    max_pages: int
    overflow_text: str
    overflow_word_count: int

    @property
    def has_overflow(self) -> bool:
        return self.page_count > self.max_pages

    @property
    def overflow_page_number(self) -> int:
        return self.max_pages + 1


APPLESCRIPT = r"""
using terms from application "Pages"
    on run argv
        set inputPath to POSIX file (item 1 of argv)
        set outputFile to POSIX file (item 2 of argv)
        set targetFileHFSpath to outputFile as text

        tell application "Pages"
            with timeout of 1800 seconds
                activate
                open inputPath
                delay 1
                set docRef to front document
                export docRef to file targetFileHFSpath as PDF
                close docRef saving no
            end timeout
        end tell
    end run
end using terms from
"""


def export_docx_to_pdf(docx_path: str | Path, pdf_path: str | Path) -> Path:
    """Open a DOCX file in Pages and export it to PDF."""

    if platform.system() != "Darwin":
        raise PdfExportError("PDF export requires macOS because it depends on Apple Pages.")

    docx_path = Path(docx_path).expanduser().resolve()
    pdf_path = Path(pdf_path).expanduser().resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Exporting %s to PDF via Apple Pages", docx_path)
    try:
        subprocess.run(
            ["osascript", "-", str(docx_path), str(pdf_path)],
            input=APPLESCRIPT,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - platform integration path
        stderr = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise PdfExportError(f"Pages export failed: {stderr}") from exc

    if not pdf_path.exists():
        raise PdfExportError(f"Pages reported success, but the PDF was not created: {pdf_path}")

    LOGGER.info("Saved tailored PDF to %s", pdf_path)
    return pdf_path


def validate_resume_pdf_layout(
    pdf_path: str | Path,
    *,
    max_pages: int = MAX_RESUME_PAGES,
    tiny_overflow_word_limit: int = TINY_OVERFLOW_WORD_LIMIT,
) -> None:
    """Ensure an exported resume PDF does not have a tiny spillover page."""

    layout_check = inspect_resume_pdf_layout(
        pdf_path,
        max_pages=max_pages,
    )

    if not layout_check.has_overflow:
        return

    if layout_check.overflow_word_count <= tiny_overflow_word_limit:
        overflow_preview = layout_check.overflow_text or "[no extractable text]"
        raise PdfLayoutError(
            f"Exported resume spilled onto page {layout_check.overflow_page_number} with only "
            f"{layout_check.overflow_word_count} word(s). Tighten the resume before sending.\n\n"
            f"Page {layout_check.overflow_page_number} text:\n{overflow_preview}"
        )

    LOGGER.warning(
        "Exported resume has %s pages; page %s contains %s words.",
        layout_check.page_count,
        layout_check.overflow_page_number,
        layout_check.overflow_word_count,
    )


def inspect_resume_pdf_layout(
    pdf_path: str | Path,
    *,
    max_pages: int = MAX_RESUME_PAGES,
) -> PdfLayoutCheck:
    """Inspect an exported resume PDF for page overflow."""

    pdf_path = Path(pdf_path).expanduser().resolve()
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - environment dependency path
        raise PdfLayoutError(
            "Checking PDF layout requires PyMuPDF. Install dependencies with: "
            "./.venv/bin/python -m pip install -e ."
        ) from exc

    try:
        document = pymupdf.open(pdf_path)
    except Exception as exc:  # pragma: no cover - library-specific parse failures
        raise PdfLayoutError(f"Could not inspect PDF layout for {pdf_path}: {exc}") from exc

    try:
        page_count = document.page_count
        overflow_text = ""
        overflow_word_count = 0
        if page_count > max_pages:
            overflow_page = document.load_page(max_pages)
            overflow_text = overflow_page.get_text("text", sort=True).strip()
            overflow_word_count = len(overflow_text.split())
    finally:
        document.close()

    return PdfLayoutCheck(
        pdf_path=pdf_path,
        page_count=page_count,
        max_pages=max_pages,
        overflow_text=overflow_text,
        overflow_word_count=overflow_word_count,
    )
