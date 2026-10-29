"""Standard CLI orchestration for approved selections, rendering, and one feedback cycle."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

from approved_tailoring import (
    LockedPlan, Selection, add_reserve, assemble_resume, decide_finbot, drop_lowest_priority,
    request_selection, same_content, validate_selection,
)
from bullet_library import ROLE_IDS, BulletLibrary, load_bullet_library
from config import AppConfig
from docx_editor import ResumeTemplate, build_diff_preview, extract_candidate_name, load_resume_template, write_tailored_resume
from export_pdf import PdfLayoutError, export_docx_to_pdf, inspect_resume_pdf_layout
from feedback_revision import _atomic_copy, collect_feedback_signals
from resume_models import count_words
from resume_layout import estimate_resume_space
from validate_resume_match import format_report, validate_resume

LOGGER = logging.getLogger(__name__)
MIN_EXPANSION_SPACE_PT = 72.0
EXPANSION_SAFETY_PT = 18.0


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def format_tailoring_summary(plan: LockedPlan, selection: Selection) -> str:
    lines = ["Tailoring summary", f"Role classification: {plan.role_family}", "Priority job requirements:"]
    lines.extend(f"- {requirement}" for requirement in plan.requirements)
    lines.extend([f"Finbot: {'kept in full' if plan.keep_finbot else 'removed in full'} (locked)",
                  f"Reason: {plan.finbot_rationale}", "Selected approved bullet IDs:"])
    roles = {role.role_id: role.bullet_ids for role in selection.roles}
    for role_id in ROLE_IDS:
        lines.append(f"- {role_id}: {', '.join(roles[role_id]) or '[none]'}")
    lines.append("Important unsupported requirements:")
    lines.extend(f"- {gap}" for gap in selection.unsupported_requirements)
    if not selection.unsupported_requirements:
        lines.append("- None identified in the selection review.")
    return "\n".join(lines)


def render_and_fit(*, selection: Selection, library: BulletLibrary, plan: LockedPlan,
                   template: ResumeTemplate, docx_path: Path, pdf_path: Path,
                   changes: list[str], allow_expansion: bool = True) -> Selection:
    """Enforce two pages, then optionally make one measured batch expansion."""
    initial_count = len(selection.priority_order)
    for attempt in range(initial_count + 1):
        # Validate content here too: no caller can bypass library constraints during export.
        validate_selection(selection.model_copy(update={"feedback_decisions": ()}), library=library, plan=plan)
        write_tailored_resume(
            template=template, tailoring=assemble_resume(selection, library=library, plan=plan),
            output_path=docx_path, include_finbot=plan.keep_finbot,
            role_bullet_limits=library.role_limits(),
        )
        export_docx_to_pdf(docx_path, pdf_path)
        layout = inspect_resume_pdf_layout(pdf_path)
        if not layout.has_overflow:
            if not allow_expansion:
                LOGGER.info("Automatic expansion skipped after feedback; preserving the reviewed selection.")
                return selection
            return _fill_available_space(selection=selection, library=library, plan=plan,
                                         template=template, docx_path=docx_path, pdf_path=pdf_path,
                                         changes=changes)
        LOGGER.warning("Resume has %s pages; removing the lowest-priority eligible approved bullet.", layout.page_count)
        if attempt == initial_count:
            break
        previous = selection
        try:
            selection = drop_lowest_priority(selection, library=library, plan=plan)
        except RuntimeError as exc:
            raise PdfLayoutError("Cannot fit two pages while preserving fixed content and role balance.") from exc
        removed = set(previous.priority_order) - set(selection.priority_order)
        changes.append(f"Removed {', '.join(sorted(removed))} to fit the two-page limit.")
        if sum(count_words(library.by_id()[key].text) for key in selection.priority_order) >= sum(
            count_words(library.by_id()[key].text) for key in previous.priority_order
        ):
            raise PdfLayoutError("Page fitting did not reduce the approved content.")
    raise PdfLayoutError("Could not fit two pages with the locked Finbot decision and fixed formatting.")


def _fill_available_space(*, selection: Selection, library: BulletLibrary, plan: LockedPlan,
                          template: ResumeTemplate, docx_path: Path, pdf_path: Path,
                          changes: list[str]) -> Selection:
    if not selection.reserve_bullets:
        return selection
    try:
        space = estimate_resume_space(pdf_path, template)
    except Exception as exc:
        changes.append(f"Expansion skipped: could not estimate space ({exc}); retained the last valid two-page draft.")
        LOGGER.warning("Could not estimate remaining space; retaining the valid draft: %s", exc)
        return selection
    if space.remaining_height_pt < MIN_EXPANSION_SPACE_PT:
        message = f"Expansion skipped: {space.remaining_height_pt:.0f} pt of free body space is below the {MIN_EXPANSION_SPACE_PT:.0f} pt threshold."
        changes.append(message)
        LOGGER.info(message)
        return selection

    budget = space.remaining_height_pt - EXPANSION_SAFETY_PT
    by_id = library.by_id()
    reserves = {reserve.bullet_id: reserve for reserve in selection.reserve_bullets}
    candidate = selection
    added: list[str] = []
    heights: dict[str, float] = {}
    # Iterating over metadata is cheap; there are no per-bullet Word exports.
    for reserve in selection.reserve_bullets:
        bullet = by_id[reserve.bullet_id]
        height = space.role_layouts[bullet.role_id].estimate_height(bullet.text)
        if height > budget:
            changes.append(f"Deferred reserve {reserve.bullet_id}: estimated {height:.0f} pt exceeds the remaining batch budget.")
            continue
        try:
            expanded = add_reserve(candidate, reserve, library=library, plan=plan)
        except RuntimeError as exc:
            changes.append(f"Deferred reserve {reserve.bullet_id}: {exc}")
            continue
        candidate = expanded
        added.append(reserve.bullet_id)
        heights[reserve.bullet_id] = height
        budget -= height
    if not added:
        LOGGER.info("No eligible reserve batch fits the measured space; keeping the current draft.")
        return selection

    def rebuild(keys: list[str]) -> Selection:
        result = selection
        for key in keys:
            result = add_reserve(result, reserves[key], library=library, plan=plan)
        return result

    # Keep the existing DOCX/PDF untouched until a whole batch is verified. Expansion
    # requires one Word export, with at most one additional trimmed-batch verification.
    with tempfile.TemporaryDirectory(prefix="reserve-batch-", dir=docx_path.parent) as temp:
        trial_docx, trial_pdf = Path(temp) / "batch.docx", Path(temp) / "batch.pdf"

        def render_batch(value: Selection):
            write_tailored_resume(
                template=template, tailoring=assemble_resume(value, library=library, plan=plan),
                output_path=trial_docx, include_finbot=plan.keep_finbot, role_bullet_limits=library.role_limits(),
            )
            export_docx_to_pdf(trial_docx, trial_pdf)
            return inspect_resume_pdf_layout(trial_pdf)

        LOGGER.info("Testing one reserve batch (%s bullets, %.0f pt available): %s", len(added), space.remaining_height_pt, ", ".join(added))
        try:
            layout = render_batch(candidate)
            if layout.has_overflow:
                overflow = estimate_resume_space(trial_pdf, template).overflow_height_pt + EXPANSION_SAFETY_PT
                removed_height = 0.0
                removed = []
                while added and removed_height < overflow:
                    key = added.pop()
                    removed.append(key)
                    removed_height += heights[key]
                changes.append("Trimmed lowest-priority batch additions after overflow: " + ", ".join(removed))
                if not added:
                    changes.append("Reserve batch rejected: no additions remained after trimming; retained the original two-page draft.")
                    LOGGER.info("Reserve batch rejected: trimming removed every addition; keeping the original two-page draft.")
                    return selection
                candidate = rebuild(added)
                LOGGER.info("Reserve batch overflowed; verifying once after removing %s.", ", ".join(removed))
                if render_batch(candidate).has_overflow:
                    changes.append("Reserve batch still overflowed after one correction; retained the original two-page draft.")
                    LOGGER.info("Reserve batch rejected after one correction; keeping the original two-page draft.")
                    return selection
        except Exception as exc:
            changes.append(f"Reserve batch verification failed ({exc}); retained the last valid two-page draft.")
            LOGGER.warning("Reserve batch verification failed; keeping the last valid draft: %s", exc)
            return selection
        publish_pair(source_docx=trial_docx, source_pdf=trial_pdf, docx=docx_path,
                     pdf=pdf_path, archive=Path(temp))
        changes.append("Added reserve batch within two pages: " + ", ".join(added))
        LOGGER.info("Reserve batch kept within two pages: %s", ", ".join(added))
        return candidate


def publish_pair(*, source_docx: Path, source_pdf: Path, docx: Path, pdf: Path, archive: Path) -> None:
    """Restore both previous files if publication fails after replacing either one."""
    destinations = (docx, pdf)
    sources = (source_docx, source_pdf)
    backups: list[Path | None] = []
    for index, destination in enumerate(destinations):
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = archive / f"publication-backup-{index}{destination.suffix}"
        if destination.exists():
            shutil.copy2(destination, backup)
            backups.append(backup)
        else:
            backups.append(None)
    try:
        for source, destination in zip(sources, destinations, strict=True):
            _atomic_copy(source, destination)
    except BaseException as exc:
        failures = []
        for backup, destination in zip(backups, destinations, strict=True):
            try:
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    _atomic_copy(backup, destination)
            except OSError as restore_exc:
                failures.append(str(restore_exc))
        if failures:
            raise RuntimeError(f"Publication failed: {exc}; restoration failed: {'; '.join(failures)}") from exc
        raise


def run_approved_workflow(*, args, config: AppConfig) -> int:
    # Import the existing output conventions without changing experiment helpers.
    from tailor_resume import (
        RoleMetadata, derive_diagnostics_path, derive_output_paths, slugify, write_diagnostics_file,
    )

    library_path = args.bullet_library or config.bullet_library_dir
    if library_path is None:
        raise ValueError("Configure bullet_library_dir or supply --bullet-library.")
    template = load_resume_template(args.resume or config.resume_template_path)
    library = load_bullet_library(library_path)
    job_description = Path(args.jd or config.job_description_path).expanduser().read_text(encoding="utf-8").strip()
    if not job_description:
        raise ValueError("Job description file is empty.")

    LOGGER.info("Loaded %s approved bullets. Deciding Finbot relevance before selection.", len(library.bullets))
    plan = decide_finbot(template=template, library=library, job_description=job_description, model=args.model)
    validation_attempts: list[dict] = []
    request = dict(library=library, template=template, plan=plan, job_description=job_description,
                   model=args.model, validation_attempts=validation_attempts)
    draft = request_selection(**request)
    reviewed = request_selection(**request, review=draft)
    # The public orchestrator validates even if a request implementation is injected in tests.
    validate_selection(reviewed, library=library, plan=plan)
    if args.dry_run:
        print(format_tailoring_summary(plan, reviewed))
        print(build_diff_preview(template, assemble_resume(reviewed, library=library, plan=plan), include_finbot=plan.keep_finbot))
        return 0

    metadata = RoleMetadata(slugify(extract_candidate_name(template)), slugify(plan.company_name), slugify(plan.job_id))
    docx, pdf = derive_output_paths(base_dir=config.tailored_resumes_dir, metadata=metadata)
    diagnostics = derive_diagnostics_path(base_dir=config.resume_diagnostics_dir, metadata=metadata)
    if docx.resolve() == template.path:
        raise ValueError("Refusing to overwrite the source resume template.")
    archive_root = diagnostics.parent / f"{diagnostics.stem}.selection_runs"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_"), dir=archive_root))
    _write_json(archive / "locked_plan.json", plan.model_dump())
    _write_json(archive / "library.json", json.loads(library.model_context()))
    _write_json(archive / "inputs.json", {
        "model": args.model, "template": str(template.path), "bullet_library": str(library.root),
        "template_sha256": hashlib.sha256(template.path.read_bytes()).hexdigest(),
        "job_description": job_description,
    })
    _write_json(archive / "draft.json", draft.model_dump())
    _write_json(archive / "reviewed.json", reviewed.model_dump())
    selected = reviewed
    initial = reviewed
    initial_report = final_report = None
    initial_text = final_text = None
    decisions = ()
    error: str | None = None
    status = "initial_render"
    outputs_published = False
    layout_changes: list[str] = []

    def save_diagnostics() -> None:
        lines = [format_tailoring_summary(plan, selected), "", f"Status: {status}",
                 f"Model: {args.model}", f"Run archive: {archive}", f"DOCX: {docx}", f"PDF: {pdf}"]
        lines.append("\nApproved bullet provenance:")
        by_id = library.by_id()
        for role in selected.roles:
            for key in role.bullet_ids:
                lines.append(f"- {key}: {by_id[key].source_file} | {by_id[key].project}")
        if error:
            lines.append(f"Error: {error}")
        if validation_attempts:
            lines.append(f"Local validation corrections requested: {len(validation_attempts)} (see validation_attempts.json).")
        lines.extend(["", "Page-fitting changes:", *(layout_changes or ["None."])])
        if decisions:
            lines.append("\nFeedback decisions (proposal/audit; final PDF report determines remaining gaps):")
            lines.extend(f"- {d.signal_id}: {d.status}: {d.rationale} [{', '.join(d.evidence_refs)}]" for d in decisions)
        if initial_report is not None:
            lines.extend(["", "Initial Resume Match:", format_report(initial_report, initial_text)])
        if final_report is not None:
            lines.extend(["", "Tailored Resume Match:", format_report(final_report, final_text)])
        if initial_report is not None and final_report is not None:
            delta = final_report.overall_score - initial_report.overall_score
            lines.append(f"\nScore change: {initial_report.overall_score}/70 -> {final_report.overall_score}/70 ({delta:+d})")
        if not same_content(initial, selected):
            before = assemble_resume(initial, library=library, plan=plan).model_dump_json(indent=2).splitlines()
            after = assemble_resume(selected, library=library, plan=plan).model_dump_json(indent=2).splitlines()
            lines.extend(["", "Initial-to-final content diff:", *difflib.unified_diff(before, after, fromfile="initial", tofile="final", lineterm="")])
        if args.show_diff:
            lines.extend(["", build_diff_preview(template, assemble_resume(selected, library=library, plan=plan), include_finbot=plan.keep_finbot)])
        write_diagnostics_file(output_path=diagnostics, content="\n".join(lines) + "\n")
        _write_json(archive / "validation_attempts.json", validation_attempts)
        _write_json(archive / "outcome.json", {
            "status": status, "error": error, "locked_plan": plan.model_dump(),
            "final_selection": selected.model_dump() if outputs_published else None,
            "outputs_published": outputs_published, "layout_changes": layout_changes,
            "initial_score": initial_report.overall_score if initial_report else None,
            "final_score": final_report.overall_score if final_report else None,
        })

    try:
        selected = render_and_fit(selection=reviewed, library=library, plan=plan, template=template,
                                  docx_path=archive / "initial.docx", pdf_path=archive / "initial.pdf", changes=layout_changes)
        initial = selected
        _write_json(archive / "initial.json", initial.model_dump())
        publish_pair(source_docx=archive / "initial.docx", source_pdf=archive / "initial.pdf", docx=docx, pdf=pdf, archive=archive)
        outputs_published = True
        status = "initial_match"
        initial_text, initial_report = validate_resume(resume_path=pdf, job_description=job_description, model=args.model)
        final_text, final_report = initial_text, initial_report
        _write_json(archive / "initial.match.json", initial_report.model_dump())
        (archive / "initial.extracted.txt").write_text(initial_text.text, encoding="utf-8")
        status = "skipped" if args.skip_feedback_revision else "pending"
        save_diagnostics()

        if not args.skip_feedback_revision:
            signals = tuple(collect_feedback_signals(initial_report))
            _write_json(archive / "feedback_signals.json", [s.model_dump() for s in signals])
            if not signals:
                status = "no_feedback"
            else:
                LOGGER.info("Reviewing %s PDF match signals using only approved selections.", len(signals))
                status = "feedback_revision"
                feedback_request = dict(**request, previous=initial, signals=signals,
                                        report=initial_report.model_dump_json(indent=2))
                proposal = request_selection(**feedback_request)
                decisions = proposal.feedback_decisions
                _write_json(archive / "proposal.json", proposal.model_dump())
                validate_selection(proposal, library=library, plan=plan, signals=signals, original=initial)
                if same_content(proposal, initial):
                    selected = proposal
                    status = "unchanged"
                else:
                    audited = request_selection(**feedback_request, review=proposal)
                    decisions = audited.feedback_decisions
                    _write_json(archive / "audited.json", audited.model_dump())
                    validate_selection(audited, library=library, plan=plan, signals=signals, original=initial)
                    if same_content(audited, initial):
                        selected = audited
                        status = "unchanged_after_review"
                    else:
                        candidate = render_and_fit(
                            selection=audited, library=library, plan=plan, template=template,
                            docx_path=archive / "candidate.docx", pdf_path=archive / "candidate.pdf", changes=layout_changes,
                            allow_expansion=False,
                        )
                        _write_json(archive / "candidate.json", candidate.model_dump())
                        if same_content(candidate, initial):
                            selected = candidate
                            status = "unchanged_after_fitting"
                        else:
                            candidate_text, candidate_report = validate_resume(
                                resume_path=archive / "candidate.pdf", job_description=job_description, model=args.model,
                            )
                            _write_json(archive / "candidate.match.json", candidate_report.model_dump())
                            (archive / "candidate.extracted.txt").write_text(candidate_text.text, encoding="utf-8")
                            publish_pair(source_docx=archive / "candidate.docx", source_pdf=archive / "candidate.pdf",
                                         docx=docx, pdf=pdf, archive=archive)
                            selected, final_text, final_report = candidate, replace(candidate_text, path=pdf), candidate_report
                            status = "revised"
    except (Exception, KeyboardInterrupt) as exc:
        error = str(exc) or "Interrupted by user"
        status = f"failed_{status}"
        LOGGER.error("Approved tailoring failed: %s", error)
        # Until publication succeeds the selected outputs/report remain the initial pair.
        selected = initial
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        save_diagnostics()

    if outputs_published:
        print(format_tailoring_summary(plan, selected))
        print(f"DOCX: {docx}\nPDF: {pdf}\nDiagnostics: {diagnostics}")
    else:
        print(f"No resume was published. Diagnostics: {diagnostics}")
    return 1 if error else 0
