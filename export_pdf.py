"""Microsoft Word PDF export utilities for macOS."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class PdfExportError(RuntimeError):
    """Raised when the Microsoft Word export step fails."""


class PdfLayoutError(RuntimeError):
    """Raised when an exported PDF fails layout constraints."""


MAX_RESUME_PAGES = 2
TINY_OVERFLOW_WORD_LIMIT = 50
WORD_EXPORT_TIMEOUT_SECONDS = 1830
STALE_WORD_EXPORT_SECONDS = 24 * 60 * 60
WORD_STAGING_SUBDIRECTORY = "resume_modifier"
WORD_EXPORT_PREFIX = "export_"
WORD_APPLICATION_PATHS = (
    Path("/Applications/Microsoft Word.app"),
    Path.home() / "Applications/Microsoft Word.app",
)


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

    @property
    def overflow_page_count(self) -> int:
        return max(0, self.page_count - self.max_pages)


WORD_EXPORT_APPLESCRIPT = r"""
using terms from application "Microsoft Word"
    on run argv
        set inputPath to item 1 of argv
        set outputPath to item 2 of argv
        set inputFile to POSIX file inputPath

        tell application "Microsoft Word"
            with timeout of 1800 seconds
                open inputFile read only true add to recent files false revert true
                save as active document file name outputPath file format format PDF add to recent files false
                close active window
            end timeout
        end tell
    end run
end using terms from
"""


def _find_word_application() -> Path | None:
    """Return the installed Microsoft Word application bundle, if present."""

    return next((path for path in WORD_APPLICATION_PATHS if path.is_dir()), None)


def _word_staging_root() -> Path:
    """Return app-owned staging storage that Word can access without folder grants."""

    return (
        Path.home()
        / "Library"
        / "Containers"
        / "com.microsoft.Word"
        / "Data"
        / "tmp"
        / WORD_STAGING_SUBDIRECTORY
    )


def _cleanup_stale_word_exports(staging_root: Path) -> None:
    """Remove only this application's abandoned export directories older than one day."""

    cutoff = time.time() - STALE_WORD_EXPORT_SECONDS
    try:
        candidates = tuple(staging_root.iterdir())
    except OSError as exc:
        LOGGER.warning("Could not inspect Word export staging directory %s: %s", staging_root, exc)
        return

    for candidate in candidates:
        if (
            not candidate.name.startswith(WORD_EXPORT_PREFIX)
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        try:
            if candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)
        except OSError as exc:
            LOGGER.warning("Could not remove stale Word export directory %s: %s", candidate, exc)


def _format_word_export_error(exc: subprocess.CalledProcessError) -> str:
    """Turn an osascript failure into a useful Word export error."""

    detail = (exc.stderr or "").strip() or (exc.stdout or "").strip() or str(exc)
    if "-1743" in detail or "not authorized" in detail.lower():
        return (
            "Microsoft Word automation was denied. In System Settings, open Privacy & Security > "
            "Automation and allow your terminal or Python launcher to control Microsoft Word."
        )
    return f"Microsoft Word export failed: {detail}"


def export_docx_to_pdf(docx_path: str | Path, pdf_path: str | Path) -> Path:
    """Open a DOCX file in Microsoft Word and export it to PDF."""

    if platform.system() != "Darwin":
        raise PdfExportError(
            "PDF export requires macOS because it automates Microsoft Word for Mac."
        )

    if _find_word_application() is None:
        searched_paths = ", ".join(str(path) for path in WORD_APPLICATION_PATHS)
        raise PdfExportError(
            f"Microsoft Word is not installed in a supported location. Checked: {searched_paths}"
        )

    docx_path = Path(docx_path).expanduser().resolve()
    pdf_path = Path(pdf_path).expanduser().resolve()
    if not docx_path.is_file():
        raise PdfExportError(f"DOCX file not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise PdfExportError(f"Microsoft Word PDF export expects a .docx file: {docx_path}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    staging_root = _word_staging_root()
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PdfExportError(
            f"Could not create Microsoft Word export staging directory {staging_root}: {exc}"
        ) from exc
    _cleanup_stale_word_exports(staging_root)
    try:
        staging_directory = Path(
            tempfile.mkdtemp(prefix=WORD_EXPORT_PREFIX, dir=staging_root)
        )
    except OSError as exc:
        raise PdfExportError(
            f"Could not create a temporary Microsoft Word export directory: {exc}"
        ) from exc

    staged_docx_path = staging_directory / "input.docx"
    staged_pdf_path = staging_directory / "output.pdf"
    destination_temporary_path: Path | None = None

    LOGGER.info("Exporting %s to PDF via Microsoft Word", docx_path)
    try:
        try:
            shutil.copy2(docx_path, staged_docx_path)
            subprocess.run(
                ["osascript", "-", str(staged_docx_path), str(staged_pdf_path)],
                input=WORD_EXPORT_APPLESCRIPT,
                text=True,
                capture_output=True,
                check=True,
                timeout=WORD_EXPORT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - platform integration path
            raise PdfExportError(
                f"Microsoft Word export timed out after {WORD_EXPORT_TIMEOUT_SECONDS} seconds."
            ) from exc
        except subprocess.CalledProcessError as exc:  # pragma: no cover - platform integration path
            raise PdfExportError(_format_word_export_error(exc)) from exc
        except OSError as exc:
            raise PdfExportError(f"Could not stage the DOCX for Microsoft Word: {exc}") from exc

        if not staged_pdf_path.is_file():
            raise PdfExportError(
                "Microsoft Word reported success, but the staged PDF was not created: "
                f"{staged_pdf_path}"
            )
        if staged_pdf_path.stat().st_size == 0:
            raise PdfExportError(
                f"Microsoft Word created an empty PDF: {staged_pdf_path}"
            )

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{pdf_path.stem}_word_", suffix=".pdf", dir=pdf_path.parent
        )
        os.close(file_descriptor)
        destination_temporary_path = Path(temporary_name)
        shutil.copyfile(staged_pdf_path, destination_temporary_path)
        destination_temporary_path.replace(pdf_path)
        destination_temporary_path = None
    finally:
        if destination_temporary_path is not None:
            destination_temporary_path.unlink(missing_ok=True)
        try:
            shutil.rmtree(staging_directory)
        except OSError as exc:
            LOGGER.warning(
                "Could not remove Word export staging directory %s: %s",
                staging_directory,
                exc,
            )

    LOGGER.info("Saved PDF to %s", pdf_path)
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
            overflow_text = "\n\n".join(
                document.load_page(page_index).get_text("text", sort=True).strip()
                for page_index in range(max_pages, page_count)
            ).strip()
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
