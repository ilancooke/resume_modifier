from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import export_pdf


class ExportDocxToPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.docx_path = self.root / "resume.docx"
        self.pdf_path = self.root / "resume.pdf"
        self.docx_path.write_bytes(b"docx placeholder")

    @patch("export_pdf._find_word_application", return_value=Path("/Applications/Microsoft Word.app"))
    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf.subprocess.run")
    def test_exports_with_microsoft_word_applescript(
        self,
        run_mock,
        _system_mock,
        _word_mock,
    ) -> None:
        def create_pdf(*args, **kwargs):
            Path(args[0][3]).write_bytes(b"%PDF-1.4\ncontent")
            return subprocess.CompletedProcess(args[0], 0, "", "")

        run_mock.side_effect = create_pdf

        result = export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

        self.assertEqual(result, self.pdf_path.resolve())
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0][:3], ["osascript", "-", str(self.docx_path.resolve())])
        temporary_pdf = Path(args[0][3])
        self.assertEqual(temporary_pdf.parent, self.pdf_path.parent.resolve())
        self.assertEqual(temporary_pdf.suffix, ".pdf")
        self.assertNotEqual(temporary_pdf, self.pdf_path.resolve())
        self.assertIn('application "Microsoft Word"', kwargs["input"])
        self.assertIn("file format format PDF", kwargs["input"])
        self.assertIn("close active window", kwargs["input"])
        self.assertNotIn("saving no", kwargs["input"])
        self.assertNotIn("Pages", kwargs["input"])
        self.assertEqual(kwargs["timeout"], export_pdf.WORD_EXPORT_TIMEOUT_SECONDS)
        self.assertTrue(kwargs["check"])

    @patch("export_pdf._find_word_application", return_value=Path("/Applications/Microsoft Word.app"))
    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf.subprocess.run")
    def test_reports_missing_output(self, run_mock, _system_mock, _word_mock) -> None:
        with self.assertRaisesRegex(export_pdf.PdfExportError, "PDF was not created"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

        run_mock.assert_called_once()

    @patch("export_pdf._find_word_application", return_value=Path("/Applications/Microsoft Word.app"))
    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf.subprocess.run")
    def test_reports_empty_output(self, run_mock, _system_mock, _word_mock) -> None:
        run_mock.side_effect = lambda *args, **kwargs: Path(args[0][3]).touch()

        with self.assertRaisesRegex(export_pdf.PdfExportError, "empty PDF"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

    @patch("export_pdf._find_word_application", return_value=Path("/Applications/Microsoft Word.app"))
    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf.subprocess.run")
    def test_explains_automation_permission_failure(
        self,
        run_mock,
        _system_mock,
        _word_mock,
    ) -> None:
        run_mock.side_effect = subprocess.CalledProcessError(
            1,
            ["osascript"],
            stderr="Not authorized to send Apple events. (-1743)",
        )

        with self.assertRaisesRegex(export_pdf.PdfExportError, "Automation"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

    @patch("export_pdf._find_word_application", return_value=Path("/Applications/Microsoft Word.app"))
    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf.subprocess.run")
    def test_reports_timeout(self, run_mock, _system_mock, _word_mock) -> None:
        run_mock.side_effect = subprocess.TimeoutExpired(
            ["osascript"], export_pdf.WORD_EXPORT_TIMEOUT_SECONDS
        )

        with self.assertRaisesRegex(export_pdf.PdfExportError, "timed out"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

    @patch("export_pdf.platform.system", return_value="Darwin")
    @patch("export_pdf._find_word_application", return_value=None)
    def test_requires_word_installation(self, _word_mock, _system_mock) -> None:
        with self.assertRaisesRegex(export_pdf.PdfExportError, "not installed"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)

    @patch("export_pdf.platform.system", return_value="Linux")
    def test_requires_macos(self, _system_mock) -> None:
        with self.assertRaisesRegex(export_pdf.PdfExportError, "requires macOS"):
            export_pdf.export_docx_to_pdf(self.docx_path, self.pdf_path)


if __name__ == "__main__":
    unittest.main()
