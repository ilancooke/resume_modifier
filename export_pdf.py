"""Apple Pages PDF export utilities for macOS."""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class PdfExportError(RuntimeError):
    """Raised when the Pages export step fails."""


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
