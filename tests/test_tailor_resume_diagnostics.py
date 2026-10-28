from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
                        source_files=[
                            f"{prefix}evidence.md" if prefix.endswith("/") else prefix
                            for prefix in spec.source_prefixes
                        ],
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

    def test_main_routes_to_approved_workflow(self):
        args = SimpleNamespace(log_level="ERROR")
        with (
            patch("tailor_resume.parse_args", return_value=args),
            patch("tailor_resume.configure_logging"),
            patch("tailor_resume.load_config", return_value="config"),
            patch("approved_workflow.run_approved_workflow", return_value=0) as workflow,
        ):
            self.assertEqual(main(), 0)
        workflow.assert_called_once_with(args=args, config="config")

    def test_main_reports_workflow_errors(self):
        with (
            patch("tailor_resume.parse_args", return_value=SimpleNamespace(log_level="ERROR")),
            patch("tailor_resume.configure_logging"),
            patch("tailor_resume.load_config", return_value="config"),
            patch("approved_workflow.run_approved_workflow", side_effect=ValueError("Invalid library")),
            patch("tailor_resume.LOGGER.error") as log,
        ):
            self.assertEqual(main(), 1)
        self.assertIn("Invalid library", str(log.call_args))


if __name__ == "__main__":
    unittest.main()
