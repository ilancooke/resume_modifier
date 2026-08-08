from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import AppConfig
from resume_models import EvidenceBullet, JobMetadata, ROLE_SPECS, TailoredResume, TailoredRole
from tailor_resume import (
    RoleMetadata,
    derive_diagnostics_path,
    format_diagnostics_report,
    main,
    write_diagnostics_file,
)


def build_tailoring() -> TailoredResume:
    return TailoredResume(
        summary="Evidence-based professional summary.",
        summary_source_files=["Amazon/Data Scientist/evidence.md"],
        roles=[
            TailoredRole(
                role_id=spec.role_id,
                company=spec.company,
                role=spec.role,
                bullets=[
                    EvidenceBullet(
                        text=f"{spec.role_id} bullet {index}",
                        source_files=[f"{spec.source_prefix}evidence.md"],
                    )
                    for index in range(1, spec.preferred_bullets + 1)
                ],
            )
            for spec in ROLE_SPECS
        ],
        metadata=JobMetadata(company_name="Example", job_id="123"),
    )


class TailorResumeDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.metadata = RoleMetadata(
            candidate_slug="ilan_cooke",
            company_slug="example",
            role_number_slug="123",
        )

    def test_writes_resume_specific_diagnostics_file(self) -> None:
        path = derive_diagnostics_path(base_dir=self.root, metadata=self.metadata)
        self.assertEqual(path.name, "ilan_cooke_example_123.diagnostics.txt")

        result = write_diagnostics_file(output_path=path, content="feedback\n")

        self.assertEqual(result, path.resolve())
        self.assertEqual(path.read_text(encoding="utf-8"), "feedback\n")

    def test_diagnostics_report_contains_content_and_match_feedback(self) -> None:
        report = format_diagnostics_report(
            tailoring=build_tailoring(),
            template=object(),
            metadata=self.metadata,
            model="gpt-5.6-sol",
            docx_output_path=self.root / "resume.docx",
            pdf_output_path=self.root / "resume.pdf",
            match_report="Tailored Resume Match:\nOverall: 60/70",
            include_diff=False,
        )

        self.assertIn("Tailored Resume Diagnostics", report)
        self.assertIn("Model: gpt-5.6-sol", report)
        self.assertIn("Generated resume content:", report)
        self.assertIn("Tailored Resume Match:\nOverall: 60/70", report)
        self.assertNotIn("Placeholder-to-content diff:", report)

    def test_dry_run_does_not_write_diagnostics(self) -> None:
        job_description_path = self.root / "job.txt"
        job_description_path.write_text("Example job description", encoding="utf-8")
        config = AppConfig(
            resume_template_path=self.root / "stub.docx",
            career_history_dir=self.root / "career-history",
            job_description_path=job_description_path,
            tailored_resumes_dir=self.root / "roles",
            resume_diagnostics_dir=self.root / "resume_diagnostics",
        )
        args = SimpleNamespace(
            resume=None,
            career_history=None,
            jd=None,
            model="gpt-5.6-sol",
            dry_run=True,
            show_diff=False,
            log_level="ERROR",
        )

        with redirect_stdout(StringIO()):
            with (
                patch("tailor_resume.parse_args", return_value=args),
                patch("tailor_resume.configure_logging"),
                patch("tailor_resume.load_config", return_value=config),
                patch(
                    "tailor_resume.load_resume_template",
                    return_value=SimpleNamespace(path=config.resume_template_path),
                ),
                patch("tailor_resume.load_career_history", return_value=SimpleNamespace(files=[], excluded_files=[])),
                patch("tailor_resume.request_tailoring", return_value=build_tailoring()),
                patch("tailor_resume.extract_candidate_name", return_value="Ilan Cooke"),
                patch("tailor_resume.build_diff_preview", return_value="preview"),
                patch("tailor_resume.write_diagnostics_file") as write_feedback,
            ):
                self.assertEqual(main(), 0)

        write_feedback.assert_not_called()

    def test_completed_run_writes_diagnostics(self) -> None:
        job_description_path = self.root / "job.txt"
        job_description_path.write_text("Example job description", encoding="utf-8")
        config = AppConfig(
            resume_template_path=self.root / "stub.docx",
            career_history_dir=self.root / "career-history",
            job_description_path=job_description_path,
            tailored_resumes_dir=self.root / "roles",
            resume_diagnostics_dir=self.root / "resume_diagnostics",
        )
        args = SimpleNamespace(
            resume=None,
            career_history=None,
            jd=None,
            model="gpt-5.6-sol",
            dry_run=False,
            show_diff=False,
            log_level="ERROR",
        )
        tailoring = build_tailoring()
        docx_path = self.root / "roles" / "example" / "resume.docx"
        pdf_path = docx_path.with_suffix(".pdf")

        with redirect_stdout(StringIO()):
            with (
                patch("tailor_resume.parse_args", return_value=args),
                patch("tailor_resume.configure_logging"),
                patch("tailor_resume.load_config", return_value=config),
                patch(
                    "tailor_resume.load_resume_template",
                    return_value=SimpleNamespace(path=config.resume_template_path),
                ),
                patch("tailor_resume.load_career_history", return_value=SimpleNamespace(files=[], excluded_files=[])),
                patch("tailor_resume.request_tailoring", return_value=tailoring),
                patch("tailor_resume.extract_candidate_name", return_value="Ilan Cooke"),
                patch("tailor_resume.derive_output_paths", return_value=(docx_path, pdf_path)),
                patch("tailor_resume.write_tailored_resume"),
                patch("tailor_resume.export_docx_to_pdf"),
                patch("tailor_resume.repair_pdf_overflow", return_value=tailoring),
                patch("tailor_resume.run_tailored_match_validation", return_value="match report"),
                patch("tailor_resume.write_diagnostics_file") as write_feedback,
            ):
                self.assertEqual(main(), 0)

        write_feedback.assert_called_once()
        self.assertEqual(
            write_feedback.call_args.kwargs["output_path"],
            self.root / "resume_diagnostics" / "ilan_cooke_example_123.diagnostics.txt",
        )
        self.assertIn("match report", write_feedback.call_args.kwargs["content"])

    def test_match_validation_failure_still_writes_diagnostics(self) -> None:
        job_description_path = self.root / "job.txt"
        job_description_path.write_text("Example job description", encoding="utf-8")
        config = AppConfig(
            resume_template_path=self.root / "stub.docx",
            career_history_dir=self.root / "career-history",
            job_description_path=job_description_path,
            tailored_resumes_dir=self.root / "roles",
            resume_diagnostics_dir=self.root / "resume_diagnostics",
        )
        args = SimpleNamespace(
            resume=None,
            career_history=None,
            jd=None,
            model="gpt-5.6-sol",
            dry_run=False,
            show_diff=False,
            log_level="ERROR",
        )
        tailoring = build_tailoring()
        docx_path = self.root / "roles" / "example" / "resume.docx"
        pdf_path = docx_path.with_suffix(".pdf")

        with redirect_stdout(StringIO()):
            with (
                patch("tailor_resume.parse_args", return_value=args),
                patch("tailor_resume.configure_logging"),
                patch("tailor_resume.load_config", return_value=config),
                patch(
                    "tailor_resume.load_resume_template",
                    return_value=SimpleNamespace(path=config.resume_template_path),
                ),
                patch("tailor_resume.load_career_history", return_value=SimpleNamespace(files=[], excluded_files=[])),
                patch("tailor_resume.request_tailoring", return_value=tailoring),
                patch("tailor_resume.extract_candidate_name", return_value="Ilan Cooke"),
                patch("tailor_resume.derive_output_paths", return_value=(docx_path, pdf_path)),
                patch("tailor_resume.write_tailored_resume"),
                patch("tailor_resume.export_docx_to_pdf"),
                patch("tailor_resume.repair_pdf_overflow", return_value=tailoring),
                patch(
                    "tailor_resume.run_tailored_match_validation",
                    side_effect=RuntimeError("match service unavailable"),
                ),
                patch("tailor_resume.write_diagnostics_file") as write_feedback,
                patch("tailor_resume.LOGGER.error"),
            ):
                self.assertEqual(main(), 1)

        write_feedback.assert_called_once()
        self.assertIn("Match validation failed: match service unavailable", write_feedback.call_args.kwargs["content"])


if __name__ == "__main__":
    unittest.main()
