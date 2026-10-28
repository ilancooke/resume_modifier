"""One evidence-grounded feedback cycle with staged exports and initial-file recovery."""

from __future__ import annotations

import difflib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from career_history import CareerHistory
from docx_editor import ResumeTemplate, write_tailored_resume
from export_pdf import export_docx_to_pdf
from generation_metrics import ModelCallMetrics
from match_client import ResumeMatchReport
from openai_client import compress_tailored_resume, request_feedback_revision, validate_feedback_revision
from resume_models import FeedbackDecision, FeedbackSignal, TailoredResume
from resume_reader import ResumeText
from validate_resume_match import format_report, validate_resume

LOGGER = logging.getLogger(__name__)


@dataclass
class FeedbackOutcome:
    """Only the selected resume and its matching evaluation are exposed to the caller."""

    tailoring: TailoredResume
    resume_text: ResumeText
    report: ResumeMatchReport
    initial_tailoring: TailoredResume
    initial_report: ResumeMatchReport
    initial_resume_text: ResumeText
    status: str = "no_feedback"
    archive_dir: Path | None = None
    decisions: list[FeedbackDecision] = field(default_factory=list)
    signals: list[FeedbackSignal] = field(default_factory=list)
    error: str | None = None


def collect_feedback_signals(report: ResumeMatchReport) -> list[FeedbackSignal]:
    """Use observations and recommendations, not score cutoffs, to select feedback."""

    texts = [*report.missing_or_weak_signals, *report.recommended_resume_changes, *report.knockout_risks]
    for dimension in report.dimension_scores:
        texts.extend(dimension.missing_or_weak_signals)
    seen: set[str] = set()
    signals = []
    for text in texts:
        text = " ".join(text.split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        signals.append(FeedbackSignal(signal_id=f"signal_{len(signals) + 1}", text=text))
    return signals


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _atomic_copy(source: Path, destination: Path) -> None:
    """Stage on the destination filesystem before replacing one published file."""

    descriptor, name = tempfile.mkstemp(prefix=f".{destination.stem}_", suffix=destination.suffix,
                                        dir=destination.parent)
    os.close(descriptor)
    staged = Path(name)
    try:
        shutil.copy2(source, staged)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)


def run_feedback_revision(
    *, template: ResumeTemplate, career_history: CareerHistory, job_description: str,
    tailoring: TailoredResume, initial_resume_text: ResumeText, initial_report: ResumeMatchReport,
    docx_output_path: Path, pdf_output_path: Path, diagnostics_output_path: Path,
    model: str, repair_layout: Callable[..., TailoredResume],
) -> FeedbackOutcome:
    """Revise at most once; retain initial outputs on any unsuccessful revision stage."""

    outcome = FeedbackOutcome(
        tailoring=tailoring, resume_text=initial_resume_text, report=initial_report,
        initial_tailoring=tailoring, initial_report=initial_report,
        initial_resume_text=initial_resume_text, signals=collect_feedback_signals(initial_report),
    )
    if not outcome.signals:
        LOGGER.info("No match feedback requires revision; keeping the initial resume.")
        return outcome

    calls: list[ModelCallMetrics] = []
    promotion_started = False
    final_match_requested = False
    outcome.status = "in_progress"
    archive: Path | None = None
    try:
        archive_root = diagnostics_output_path.parent / f"{diagnostics_output_path.stem}.feedback_runs"
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%dT%H%M%S_"), dir=archive_root))
        outcome.archive_dir = archive
        shutil.copy2(docx_output_path, archive / "initial.docx")
        shutil.copy2(pdf_output_path, archive / "initial.pdf")
        _write_json(archive / "initial.json", {
            "tailoring": tailoring.model_dump(), "match_report": initial_report.model_dump(),
            "rendered_resume_text": initial_resume_text.text,
            "signals": [signal.model_dump() for signal in outcome.signals],
        })
        (archive / "initial.match.txt").write_text(format_report(initial_report, initial_resume_text) + "\n", encoding="utf-8")

        request = dict(
            career_history_context=career_history.model_context(),
            allowed_sources_by_role=career_history.allowed_sources_by_role(),
            job_description=job_description, tailoring=tailoring,
            rendered_resume_text=initial_resume_text.text,
            match_report_json=initial_report.model_dump_json(indent=2),
            signals=outcome.signals, model=model, calls=calls,
        )
        LOGGER.info("Reviewing %s match signal(s) against the full career history", len(outcome.signals))
        proposal = request_feedback_revision(**request)
        outcome.decisions = proposal.decisions
        _write_json(archive / "proposal.json", proposal.model_dump())
        if proposal.tailoring == tailoring:
            outcome.status = "unchanged"
            LOGGER.info("Feedback revision found no useful content changes; keeping the initial resume.")
        else:
            LOGGER.info("Checking the feedback revision against the full career history")
            audited = request_feedback_revision(**request, proposal=proposal)
            outcome.decisions = audited.decisions
            _write_json(archive / "audited.json", audited.model_dump())
            if audited.tailoring == tailoring:
                outcome.status = "unchanged_after_review"
            else:
                candidate_docx = archive / "candidate.docx"
                candidate_pdf = archive / "candidate.pdf"
                write_tailored_resume(template=template, tailoring=audited.tailoring, output_path=candidate_docx)
                export_docx_to_pdf(candidate_docx, candidate_pdf)
                objectives = json.dumps([
                    {"signal": signal.text, "repair": decision.rationale}
                    for signal, decision in zip(outcome.signals, audited.decisions, strict=True)
                    if decision.status == "addressed"
                ], ensure_ascii=False)

                def compress(**kwargs) -> TailoredResume:
                    return compress_tailored_resume(**kwargs, model=model, calls=calls,
                                                     revision_objectives=objectives)

                candidate = repair_layout(
                    template=template, docx_output_path=candidate_docx, pdf_output_path=candidate_pdf,
                    tailoring=audited.tailoring, model=model, compressor=compress,
                )
                # Compression preserves citation lists; recheck all local invariants before publication.
                checked = validate_feedback_revision(
                    revision=audited.model_copy(update={"tailoring": candidate}), original=tailoring,
                    signals=outcome.signals, allowed_sources_by_role=career_history.allowed_sources_by_role(),
                )
                _write_json(archive / "candidate.json", checked.model_dump())
                LOGGER.info("Evaluating the revised PDF once; this ends the feedback cycle")
                # This evaluator sees only the candidate PDF and JD, never its earlier judgment.
                final_match_requested = True
                candidate_text, candidate_report = validate_resume(
                    resume_path=candidate_pdf, job_description=job_description, model=model,
                )
                _write_json(archive / "candidate.match.json", candidate_report.model_dump())
                (archive / "candidate.match.txt").write_text(format_report(candidate_report, candidate_text) + "\n", encoding="utf-8")
                promotion_started = True
                _atomic_copy(candidate_docx, docx_output_path)
                _atomic_copy(candidate_pdf, pdf_output_path)
                outcome.tailoring = checked.tailoring
                outcome.resume_text = replace(candidate_text, path=pdf_output_path)
                outcome.report = candidate_report
                outcome.decisions = checked.decisions
                outcome.status = "revised"
    except (Exception, KeyboardInterrupt) as exc:
        outcome.status = "failed"
        outcome.error = str(exc) or "Interrupted by user"
        LOGGER.error("Feedback revision failed: %s", outcome.error)
        if promotion_started and archive is not None:
            restoration_errors = []
            for source, destination in ((archive / "initial.docx", docx_output_path),
                                        (archive / "initial.pdf", pdf_output_path)):
                try:
                    _atomic_copy(source, destination)
                except OSError as restore_exc:
                    restoration_errors.append(str(restore_exc))
            if restoration_errors:
                outcome.error += "; failed to restore initial outputs: " + "; ".join(restoration_errors)
        # Selected content/report remain the initial pair unless both files were promoted.
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        if archive is not None:
            try:
                _write_json(archive / "outcome.json", {
                    "status": outcome.status, "error": outcome.error, "model": model,
                    "initial_score": initial_report.overall_score,
                    "selected_score": outcome.report.overall_score,
                    "selected_tailoring": outcome.tailoring.model_dump(),
                    "decisions": [decision.model_dump() for decision in outcome.decisions],
                    "generation_calls": [call.model_dump() for call in calls],
                    "final_match_requested": final_match_requested,
                })
            except OSError as exc:
                outcome.error = f"{outcome.error + '; ' if outcome.error else ''}Could not save feedback outcome: {exc}"
                LOGGER.error("%s", outcome.error)
    return outcome


def format_feedback_outcome(outcome: FeedbackOutcome) -> str:
    """Keep the initial evaluation, decisions, actual content diff, and final score together."""

    lines = ["Feedback revision:", f"Status: {outcome.status}"]
    if outcome.archive_dir:
        lines.append(f"Initial files and revision artifacts: {outcome.archive_dir}")
    if outcome.error:
        lines.append(f"Revision error: {outcome.error}")
    texts = {signal.signal_id: signal.text for signal in outcome.signals}
    lines.append("Decisions below describe the proposal/audit; the final PDF match report determines remaining weak signals.")
    for decision in outcome.decisions:
        lines.extend([
            f"- {texts[decision.signal_id]} [{decision.status}]",
            f"  {decision.rationale}",
            "  Sources: " + (", ".join(decision.source_files) or "none"),
        ])
    lines.extend(["", "Initial Resume Match:", format_report(outcome.initial_report, outcome.initial_resume_text)])
    if outcome.status == "revised":
        delta = outcome.report.overall_score - outcome.initial_report.overall_score
        lines.extend(["", f"Score change: {outcome.initial_report.overall_score}/70 -> {outcome.report.overall_score}/70 ({delta:+d})"])
        initial_scores = {score.name: score.score for score in outcome.initial_report.dimension_scores}
        for score in outcome.report.dimension_scores:
            previous = initial_scores[score.name]
            lines.append(f"- {score.name}: {previous} -> {score.score} ({score.score - previous:+d})")
        before = outcome.initial_tailoring.model_dump_json(indent=2).splitlines()
        after = outcome.tailoring.model_dump_json(indent=2).splitlines()
        lines.extend(["", "Initial-to-final attributed-content diff:", *difflib.unified_diff(
            before, after, fromfile="initial", tofile="final", lineterm="",
        )])
    return "\n".join(lines)
