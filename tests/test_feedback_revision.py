from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from career_history import CareerHistory, CareerHistoryFile
from config import AppConfig
from feedback_revision import collect_feedback_signals, format_feedback_outcome, run_feedback_revision
from match_client import DimensionScore, JobCriteriaSummary, ResumeMatchReport
from match_prompts import MATCH_DIMENSIONS
from openai_client import ResumeTailoringError, request_feedback_revision, validate_feedback_revision
from resume_models import (
    EvidenceBullet, FeedbackDecision, FeedbackRevision, JobMetadata, ROLE_SPECS,
    TailoredResume, TailoredRole,
)
from resume_reader import ResumeText
from tailor_resume import main


INCIDENT_SOURCE = "Amazon/Data Scientist/MLOps and Operational Excellence.md"


def make_report(*, weak: bool = True, first_score: int = 8) -> ResumeMatchReport:
    scores = [DimensionScore(
        name=name, score=first_score if index == 0 else 8, max_score=maximum,
        rationale="Evidence assessed.", evidence=["Production models."],
        missing_or_weak_signals=["Incident response"] if weak and index == 0 else [],
    ) for index, (name, maximum) in enumerate(MATCH_DIMENSIONS)]
    return ResumeMatchReport(
        overall_score=sum(score.score for score in scores), rating_label="Strong match",
        job_criteria_summary=JobCriteriaSummary(
            role_family="Safety", required_criteria=["Incident response"],
            preferred_criteria=[], core_responsibilities=["Investigate incidents"], hard_blockers=[],
        ),
        dimension_scores=scores, strongest_matches=["Production models"],
        missing_or_weak_signals=["Incident response"] if weak else [],
        recommended_resume_changes=[], knockout_risks=[], summary="Resume assessed.",
    )


class FeedbackRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.docx = self.root / "resume.docx"
        self.pdf = self.root / "resume.pdf"
        self.diagnostics = self.root / "resume.diagnostics.txt"
        self.docx.write_bytes(b"initial docx")
        self.pdf.write_bytes(b"initial pdf")
        sources = [
            "Amazon/Data Scientist/model.md", "Amazon/BA-BIE/history.md",
            "Petsmart/PetSmart - Manager, Business Analytics.md",
        ]
        self.original = TailoredResume(
            summary="Built production decision systems.", summary_source_files=[sources[0]],
            roles=[TailoredRole(
                role_id=spec.role_id, company=spec.company, role=spec.role,
                bullets=[EvidenceBullet(text="Built a supported decision system.", source_files=[source])],
            ) for spec, source in zip(ROLE_SPECS, sources, strict=True)],
            metadata=JobMetadata(company_name="Example", job_id="123"),
        )
        self.history = CareerHistory(
            root=self.root, excluded_files=(), files=tuple([
                CareerHistoryFile(relative_path=source, role_id=spec.role_id, text="Supported accomplishment.")
                for spec, source in zip(ROLE_SPECS, sources, strict=True)
            ] + [CareerHistoryFile(
                relative_path=INCIDENT_SOURCE, role_id="amazon_data_scientist",
                text="Participated in on-call for six years and investigated production incidents with engineering owners.",
            )]),
        )
        self.initial_report = make_report()
        self.final_report = make_report(weak=False, first_score=9)
        self.signals = collect_feedback_signals(self.initial_report)
        self.initial_text = ResumeText(self.pdf, "Initial resume with fixed Skills and Finbot content.", "pdf_text", 2, [])
        self.candidate = self.original.model_copy(deep=True)
        self.candidate.roles[0].bullets.append(EvidenceBullet(
            text="Investigated production incidents during six years of on-call work, coordinating with engineering owners.",
            source_files=[INCIDENT_SOURCE],
        ))
        self.proposal = FeedbackRevision(tailoring=self.candidate, decisions=[FeedbackDecision(
            signal_id=self.signals[0].signal_id, status="addressed",
            rationale="Added actual on-call incident investigation and engineering coordination.",
            source_files=[INCIDENT_SOURCE],
        )])
        self.template = SimpleNamespace(path=self.root / "stub.docx")
        self.repair = Mock(side_effect=lambda **kwargs: kwargs["tailoring"])

    def run_cycle(self):
        return run_feedback_revision(
            template=self.template, career_history=self.history, job_description="Safety incident response role.",
            tailoring=self.original, initial_resume_text=self.initial_text, initial_report=self.initial_report,
            docx_output_path=self.docx, pdf_output_path=self.pdf, diagnostics_output_path=self.diagnostics,
            model="gpt-6.1-terra", repair_layout=self.repair,
        )

    def services(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.request = stack.enter_context(patch("feedback_revision.request_feedback_revision", side_effect=[self.proposal, self.proposal]))
        self.writer = stack.enter_context(patch("feedback_revision.write_tailored_resume", side_effect=lambda **kw: kw["output_path"].write_bytes(b"revised docx")))
        self.exporter = stack.enter_context(patch("feedback_revision.export_docx_to_pdf", side_effect=lambda docx, pdf: pdf.write_bytes(b"revised pdf")))
        self.evaluator = stack.enter_context(patch("feedback_revision.validate_resume", side_effect=lambda **kw: (
            ResumeText(kw["resume_path"], "Revised PDF with incident investigation.", "pdf_text", 2, []), self.final_report,
        )))

    def check_revision(self, revision):
        return validate_feedback_revision(
            revision=revision, original=self.original, signals=self.signals,
            allowed_sources_by_role=self.history.allowed_sources_by_role(),
        )

    def test_deduplicates_global_and_dimension_feedback(self):
        report = make_report()
        report.recommended_resume_changes = ["  Incident   response  ", "Explain reviewer capacity"]
        self.assertEqual([signal.text for signal in collect_feedback_signals(report)],
                         ["Incident response", "Explain reviewer capacity"])

    def test_both_api_calls_get_full_history_and_preserve_uncited_incident_evidence(self):
        with patch("openai_client._create_client", return_value=object()), patch(
            "openai_client._request_json", return_value=self.proposal.model_dump_json()
        ) as api:
            kwargs = dict(
                career_history_context=self.history.model_context(),
                allowed_sources_by_role=self.history.allowed_sources_by_role(),
                job_description="Safety incident response role.", tailoring=self.original,
                rendered_resume_text=self.initial_text.text,
                match_report_json=self.initial_report.model_dump_json(), signals=self.signals,
            )
            proposal = request_feedback_revision(**kwargs)
            result = request_feedback_revision(**kwargs, proposal=proposal)
        self.assertIn(INCIDENT_SOURCE, result.tailoring.roles[0].bullets[-1].source_files)
        for call in api.call_args_list:
            prompt = call.kwargs["user_prompt"]
            self.assertIn(self.history.model_context(), prompt)
            self.assertIn("Participated in on-call for six years", prompt)
            self.assertIn(self.initial_text.text, prompt)
            self.assertIn("Incident response", prompt)
        self.assertEqual(api.call_args_list[1].kwargs["request_name"], "feedback factuality review")

    def test_rejects_invalid_json_response(self):
        with patch("openai_client._create_client", return_value=object()), patch(
            "openai_client._request_json", return_value="not json"
        ), self.assertRaisesRegex(ResumeTailoringError, "validation failed"):
            request_feedback_revision(
                career_history_context=self.history.model_context(),
                allowed_sources_by_role=self.history.allowed_sources_by_role(),
                job_description="Role", tailoring=self.original, rendered_resume_text="PDF",
                match_report_json="{}", signals=self.signals,
            )

    def test_rejects_missing_or_duplicate_decisions(self):
        for decisions in ([], self.proposal.decisions * 2):
            with self.subTest(decisions=len(decisions)), self.assertRaisesRegex(ResumeTailoringError, "exactly once"):
                self.check_revision(self.proposal.model_copy(update={"decisions": decisions}))

    def test_rejects_job_metadata_change(self):
        revision = self.proposal.model_copy(deep=True)
        revision.tailoring.metadata.job_id = "other"
        with self.assertRaisesRegex(ResumeTailoringError, "metadata"):
            self.check_revision(revision)

    def test_rejects_unknown_decision_evidence(self):
        revision = self.proposal.model_copy(deep=True)
        revision.decisions[0].source_files = ["Invented.md"]
        with self.assertRaisesRegex(ResumeTailoringError, "unknown source"):
            self.check_revision(revision)

    def test_rejects_cross_role_bullet_evidence(self):
        revision = self.proposal.model_copy(deep=True)
        revision.tailoring.roles[1].bullets[0].source_files = [INCIDENT_SOURCE]
        with self.assertRaisesRegex(ResumeTailoringError, "assigned to another role"):
            self.check_revision(revision)

    def test_rejects_missing_addressed_evidence_and_excess_bullets(self):
        revision = self.proposal.model_copy(deep=True)
        revision.decisions[0].source_files = []
        with self.assertRaisesRegex(ResumeTailoringError, "requires evidence"):
            self.check_revision(revision)
        revision = self.proposal.model_copy(deep=True)
        revision.tailoring.roles[0].bullets *= 5
        with self.assertRaisesRegex(ResumeTailoringError, "at most"):
            self.check_revision(revision)

    def test_unchanged_resume_cannot_claim_repaired_signal(self):
        with self.assertRaisesRegex(ResumeTailoringError, "Unchanged resume"):
            self.check_revision(self.proposal.model_copy(update={"tailoring": self.original}))

    def test_publishes_only_after_final_pdf_evaluation_and_keeps_initial_files(self):
        self.services()
        def evaluate(**kwargs):
            self.assertEqual(self.pdf.read_bytes(), b"initial pdf")
            self.assertEqual(kwargs["resume_path"].read_bytes(), b"revised pdf")
            self.assertEqual(set(kwargs), {"resume_path", "job_description", "model"})
            return ResumeText(kwargs["resume_path"], "Revised PDF", "pdf_text", 2, []), self.final_report
        self.evaluator.side_effect = evaluate
        outcome = self.run_cycle()
        self.assertEqual(outcome.status, "revised")
        self.assertEqual(self.docx.read_bytes(), b"revised docx")
        self.assertEqual(self.pdf.read_bytes(), b"revised pdf")
        self.assertEqual(outcome.resume_text.path, self.pdf)
        self.assertEqual((outcome.archive_dir / "initial.pdf").read_bytes(), b"initial pdf")
        self.assertTrue((outcome.archive_dir / "proposal.json").is_file())
        self.assertTrue((outcome.archive_dir / "audited.json").is_file())
        self.assertEqual(json.loads((outcome.archive_dir / "outcome.json").read_text())["status"], "revised")
        self.assertEqual(self.request.call_count, 2)
        self.evaluator.assert_called_once()
        report = format_feedback_outcome(outcome)
        self.assertIn("56/70 -> 57/70 (+1)", report)
        self.assertIn("Initial-to-final attributed-content diff", report)
        self.assertIn(INCIDENT_SOURCE, report)

    def test_no_feedback_skips_additional_calls(self):
        self.services()
        self.initial_report = make_report(weak=False)
        outcome = self.run_cycle()
        self.assertEqual(outcome.status, "no_feedback")
        self.request.assert_not_called()
        self.evaluator.assert_not_called()
        self.assertIsNone(outcome.archive_dir)

    def test_unsupported_gap_retains_resume_without_review_or_export(self):
        self.services()
        self.request.side_effect = None
        self.request.return_value = FeedbackRevision(tailoring=self.original, decisions=[FeedbackDecision(
            signal_id="signal_1", status="unsupported", rationale="No career evidence supports this gap.", source_files=[],
        )])
        outcome = self.run_cycle()
        self.assertEqual(outcome.status, "unchanged")
        self.request.assert_called_once()
        self.writer.assert_not_called()
        self.evaluator.assert_not_called()
        self.assertEqual(self.pdf.read_bytes(), b"initial pdf")

    def test_audit_can_remove_proposed_claim_without_export(self):
        self.services()
        rejected = FeedbackRevision(tailoring=self.original, decisions=[FeedbackDecision(
            signal_id="signal_1", status="unsupported", rationale="The claim was not supported.", source_files=[],
        )])
        self.request.side_effect = [self.proposal, rejected]
        outcome = self.run_cycle()
        self.assertEqual(outcome.status, "unchanged_after_review")
        self.writer.assert_not_called()
        self.evaluator.assert_not_called()

    def test_failures_keep_initial_outputs_and_report(self):
        for stage in ("proposal", "audit", "export", "layout", "final_match"):
            with self.subTest(stage=stage), ExitStack() as stack:
                request = stack.enter_context(patch("feedback_revision.request_feedback_revision", side_effect=[self.proposal, self.proposal]))
                stack.enter_context(patch("feedback_revision.write_tailored_resume", side_effect=lambda **kw: kw["output_path"].write_bytes(b"candidate")))
                exporter = stack.enter_context(patch("feedback_revision.export_docx_to_pdf", side_effect=lambda docx, pdf: pdf.write_bytes(b"candidate")))
                evaluator = stack.enter_context(patch("feedback_revision.validate_resume", side_effect=RuntimeError("final match failed")))
                self.repair.side_effect = lambda **kw: kw["tailoring"]
                if stage == "proposal": request.side_effect = RuntimeError("proposal failed")
                if stage == "audit": request.side_effect = [self.proposal, RuntimeError("audit failed")]
                if stage == "export": exporter.side_effect = RuntimeError("export failed")
                if stage == "layout": self.repair.side_effect = RuntimeError("still three pages")
                outcome = self.run_cycle()
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.report, self.initial_report)
                self.assertEqual(outcome.tailoring, self.original)
                self.assertEqual(self.docx.read_bytes(), b"initial docx")
                self.assertEqual(self.pdf.read_bytes(), b"initial pdf")
                self.assertIsNotNone(outcome.error)
                if stage != "final_match": evaluator.assert_not_called()

    def test_partial_promotion_rolls_back_both_files(self):
        import feedback_revision
        self.services()
        real_copy = feedback_revision._atomic_copy
        def copy(source, destination):
            if source.name == "candidate.pdf":
                raise OSError("Cannot publish PDF")
            real_copy(source, destination)
        with patch("feedback_revision._atomic_copy", side_effect=copy):
            outcome = self.run_cycle()
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(self.docx.read_bytes(), b"initial docx")
        self.assertEqual(self.pdf.read_bytes(), b"initial pdf")
        self.assertEqual(outcome.report, self.initial_report)

    def test_compression_gets_supported_repair_objectives(self):
        self.services()
        def repair(**kwargs):
            return kwargs["compressor"](tailoring=kwargs["tailoring"], overflow_text="overflow", overflow_word_count=5, attempt=1)
        self.repair.side_effect = repair
        with patch("feedback_revision.compress_tailored_resume", return_value=self.candidate) as compressor:
            outcome = self.run_cycle()
        self.assertEqual(outcome.status, "revised")
        self.assertIn("Incident response", compressor.call_args.kwargs["revision_objectives"])
        self.assertIn("on-call", compressor.call_args.kwargs["revision_objectives"])

    def test_interrupt_during_publication_restores_initial_files(self):
        import feedback_revision
        self.services()
        real_copy = feedback_revision._atomic_copy
        def copy(source, destination):
            if source.name == "candidate.pdf":
                raise KeyboardInterrupt()
            real_copy(source, destination)
        with patch("feedback_revision._atomic_copy", side_effect=copy), self.assertRaises(KeyboardInterrupt):
            self.run_cycle()
        self.assertEqual(self.docx.read_bytes(), b"initial docx")
        self.assertEqual(self.pdf.read_bytes(), b"initial pdf")
        record_path = next(self.root.glob("*.feedback_runs/*/outcome.json"))
        self.assertEqual(json.loads(record_path.read_text())["status"], "failed")

    def test_score_decline_does_not_trigger_retry_or_discard_valid_revision(self):
        self.services()
        self.final_report = make_report(weak=False, first_score=7)
        outcome = self.run_cycle()
        self.assertEqual(outcome.status, "revised")
        self.assertEqual(outcome.report.overall_score, 55)
        self.assertEqual(self.request.call_count, 2)
        self.evaluator.assert_called_once()

    def run_cli(self, *, fail_revision=False):
        self.services()
        if fail_revision:
            self.request.side_effect = RuntimeError("Feedback API unavailable")
        args = SimpleNamespace(resume=None, career_history=None, jd=None, model="gpt-6.1-terra",
                               dry_run=False, show_diff=False, log_level="ERROR", skip_feedback_revision=False)
        jd = self.root / "job.txt"
        jd.write_text("Safety incident response role.")
        config = AppConfig(self.template.path, self.root, jd, self.root, self.root)
        with ExitStack() as stack, redirect_stdout(StringIO()):
            for target, value in (
                ("parse_args", args), ("load_config", config), ("load_resume_template", self.template),
                ("load_career_history", self.history), ("request_tailoring", self.original),
                ("extract_candidate_name", "Candidate"), ("derive_output_paths", (self.docx, self.pdf)),
                ("derive_diagnostics_path", self.diagnostics),
                ("run_tailored_match_validation", (self.initial_text, self.initial_report)),
            ):
                stack.enter_context(patch(f"tailor_resume.{target}", return_value=value))
            stack.enter_context(patch("tailor_resume.configure_logging"))
            stack.enter_context(patch("tailor_resume.write_tailored_resume"))
            stack.enter_context(patch("tailor_resume.export_docx_to_pdf"))
            stack.enter_context(patch("tailor_resume.repair_pdf_overflow", side_effect=lambda **kw: kw["tailoring"]))
            result = main()
        return result, self.diagnostics.read_text()

    def test_cli_default_cycle_writes_diagnostics_for_selected_final_pdf(self):
        result, diagnostics = self.run_cli()
        self.assertEqual(result, 0)
        self.assertIn("Status: revised", diagnostics)
        self.assertIn("Initial Resume Match:", diagnostics)
        self.assertIn("Overall: 56/70", diagnostics)
        self.assertIn("Tailored Resume Match:\nOverall: 57/70", diagnostics)
        self.assertIn("Investigated production incidents", diagnostics)
        self.assertNotIn("Feedback revision: pending", diagnostics)

    def test_cli_revision_failure_returns_nonzero_and_initial_diagnostics(self):
        result, diagnostics = self.run_cli(fail_revision=True)
        self.assertEqual(result, 1)
        self.assertIn("Status: failed", diagnostics)
        self.assertIn("Feedback API unavailable", diagnostics)
        self.assertIn("Tailored Resume Match:\nOverall: 56/70", diagnostics)
        self.assertEqual(self.docx.read_bytes(), b"initial docx")
        self.assertEqual(self.pdf.read_bytes(), b"initial pdf")


if __name__ == "__main__":
    unittest.main()
