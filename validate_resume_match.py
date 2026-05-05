"""CLI entry point for validating resume fit against a job description."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from config import ConfigError, load_config
from match_client import ResumeMatchError, ResumeMatchReport, generate_resume_match_report
from match_prompts import MATCH_DIMENSIONS, MATCH_MAX_SCORE
from resume_reader import ResumeReadError, ResumeText, extract_resume_text

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rate how well a PDF resume matches a job description."
    )
    resume_input = parser.add_mutually_exclusive_group(required=True)
    resume_input.add_argument(
        "--resume",
        help="Path to the resume PDF file.",
    )
    resume_input.add_argument(
        "--resume-dir",
        help="Path to a folder containing resume PDF files.",
    )
    parser.add_argument(
        "--jd",
        default=None,
        help="Path to a text file containing the job description. Defaults to the value in config.json.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4-mini",
        help="OpenAI model to use for the structured match validation call.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path for writing the structured match report JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output folder for batch mode. Writes one JSON report per resume and summary.csv.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    args = parser.parse_args()
    if args.resume_dir and not args.output_dir:
        parser.error("--output-dir is required when using --resume-dir.")
    if args.resume_dir and args.output_json:
        parser.error("--output-json is only supported with --resume. Use --output-dir for batch mode.")
    if args.resume and args.output_dir:
        parser.error("--output-dir is only supported with --resume-dir. Use --output-json for single-resume mode.")

    return args


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    try:
        job_description_path = _resolve_job_description_path(args.jd)
        job_description = job_description_path.read_text(encoding="utf-8").strip()
        if not job_description:
            raise ValueError("Job description file is empty.")

        if args.resume:
            run_single(
                resume_path=Path(args.resume).expanduser().resolve(),
                job_description=job_description,
                job_description_path=job_description_path,
                model=args.model,
                output_json=Path(args.output_json).expanduser().resolve()
                if args.output_json
                else None,
            )
            return 0

        return run_batch(
            resume_dir=Path(args.resume_dir).expanduser().resolve(),
            output_dir=Path(args.output_dir).expanduser().resolve(),
            job_description=job_description,
            job_description_path=job_description_path,
            model=args.model,
        )
    except (OSError, ValueError, ConfigError, ResumeReadError, ResumeMatchError) as exc:
        LOGGER.error("%s", exc)
        return 1


def _resolve_job_description_path(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()

    return load_config().job_description_path


def run_single(
    *,
    resume_path: Path,
    job_description: str,
    job_description_path: Path,
    model: str,
    output_json: Path | None,
) -> None:
    resume_text, report = validate_resume(
        resume_path=resume_path,
        job_description=job_description,
        model=model,
    )

    print(format_report(report, resume_text))

    if output_json:
        write_report_json(
            output_path=output_json,
            report=report,
            resume_text=resume_text,
            job_description_path=job_description_path,
        )
        LOGGER.info("Saved structured match report to %s", output_json)


def run_batch(
    *,
    resume_dir: Path,
    output_dir: Path,
    job_description: str,
    job_description_path: Path,
    model: str,
) -> int:
    if not resume_dir.is_dir():
        raise ValueError(f"Resume folder not found: {resume_dir}")

    resume_paths = sorted(resume_dir.glob("*.pdf"), key=lambda path: path.name.lower())
    if not resume_paths:
        raise ValueError(f"No PDF resumes found in folder: {resume_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    failures = 0

    for resume_path in resume_paths:
        LOGGER.info("Validating resume %s", resume_path)
        output_path = output_dir / f"{resume_path.stem}.match.json"
        try:
            resume_text, report = validate_resume(
                resume_path=resume_path,
                job_description=job_description,
                model=model,
            )
            write_report_json(
                output_path=output_path,
                report=report,
                resume_text=resume_text,
                job_description_path=job_description_path,
            )
            rows.append(_success_summary_row(resume_path=resume_path, report=report))
            LOGGER.info("Saved structured match report to %s", output_path)
        except (OSError, ValueError, ResumeReadError, ResumeMatchError) as exc:
            failures += 1
            rows.append(_error_summary_row(resume_path=resume_path, error=str(exc)))
            write_error_json(output_path=output_path, resume_path=resume_path, error=str(exc))
            LOGGER.error("Failed to validate %s: %s", resume_path, exc)

    summary_path = output_dir / "summary.csv"
    write_summary_csv(summary_path=summary_path, rows=rows)
    LOGGER.info("Saved batch summary to %s", summary_path)

    print(format_batch_summary(rows=rows, summary_path=summary_path))
    return 1 if failures else 0


def validate_resume(
    *,
    resume_path: Path,
    job_description: str,
    model: str,
) -> tuple[ResumeText, ResumeMatchReport]:
    resume_text = extract_resume_text(resume_path)
    report = generate_resume_match_report(
        resume_text=resume_text.text,
        job_description=job_description,
        model=model,
    )
    return resume_text, report


def format_report(report: ResumeMatchReport, resume_text: ResumeText) -> str:
    lines = [
        f"Overall: {report.overall_score}/{MATCH_MAX_SCORE} ({report.rating_label})",
        "",
        report.summary.strip(),
        "",
        "Job Criteria:",
        f"- Role family: {report.job_criteria_summary.role_family}",
    ]
    lines.extend(_format_list("Required", report.job_criteria_summary.required_criteria))
    lines.extend(_format_list("Preferred", report.job_criteria_summary.preferred_criteria))
    lines.extend(_format_list("Hard blockers", report.job_criteria_summary.hard_blockers))
    lines.extend(["", "Dimension Scores:"])

    for dimension in report.dimension_scores:
        lines.append(f"- {dimension.name}: {dimension.score}/{dimension.max_score}")
        lines.append(f"  {dimension.rationale.strip()}")

    lines.extend(["", "Strongest Matches:"])
    lines.extend(_format_bullets(report.strongest_matches))
    lines.extend(["", "Missing Or Weak Signals:"])
    lines.extend(_format_bullets(report.missing_or_weak_signals))
    lines.extend(["", "Knockout Risks:"])
    lines.extend(_format_bullets(report.knockout_risks))
    lines.extend(["", "Recommended Resume Changes:"])
    lines.extend(_format_bullets(report.recommended_resume_changes))

    if resume_text.extraction_warnings:
        lines.extend(["", "Extraction Warnings:"])
        lines.extend(_format_bullets(resume_text.extraction_warnings))

    return "\n".join(lines)


def write_report_json(
    *,
    output_path: Path,
    report: ResumeMatchReport,
    resume_text: ResumeText,
    job_description_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "resume_path": str(resume_text.path),
        "resume_source_type": resume_text.source_type,
        "resume_page_count": resume_text.page_count,
        "extraction_warnings": resume_text.extraction_warnings,
        "job_description_path": str(job_description_path),
        "report": report.model_dump(),
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_error_json(*, output_path: Path, resume_path: Path, error: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "resume_path": str(resume_path),
        "status": "error",
        "error": error,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_summary_csv(*, summary_path: Path, rows: list[dict[str, str]]) -> None:
    sorted_rows = sorted(rows, key=_summary_sort_key)
    headers = _summary_headers()
    with summary_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_rows)


def format_batch_summary(*, rows: list[dict[str, str]], summary_path: Path) -> str:
    sorted_rows = sorted(rows, key=_summary_sort_key)
    success_count = sum(1 for row in rows if row["status"] == "success")
    error_count = len(rows) - success_count
    lines = [
        f"Processed {len(rows)} resume(s): {success_count} succeeded, {error_count} failed.",
        f"Summary CSV: {summary_path}",
        "",
        "Ranked Results:",
    ]

    for row in sorted_rows:
        if row["status"] == "success":
            lines.append(
                f"- {row['resume_file']}: {row['overall_score']}/{MATCH_MAX_SCORE} ({row['rating_label']})"
            )
        else:
            lines.append(f"- {row['resume_file']}: ERROR - {row['error']}")

    return "\n".join(lines)


def _success_summary_row(*, resume_path: Path, report: ResumeMatchReport) -> dict[str, str]:
    row = _base_summary_row(resume_path=resume_path, status="success", error="")
    row["overall_score"] = str(report.overall_score)
    row["rating_label"] = report.rating_label
    row["summary"] = report.summary.strip()

    scores_by_dimension = {dimension.name: dimension.score for dimension in report.dimension_scores}
    for name, _ in MATCH_DIMENSIONS:
        row[_dimension_column_name(name)] = str(scores_by_dimension.get(name, ""))

    return row


def format_dimension_comparison_table(
    *,
    base_report: ResumeMatchReport,
    tailored_report: ResumeMatchReport,
) -> str:
    """Format dimension-level score movement from base to tailored resume."""

    base_scores = _scores_by_dimension(base_report)
    tailored_scores = _scores_by_dimension(tailored_report)
    rows: list[tuple[str, str, str, str]] = []
    for name, _ in MATCH_DIMENSIONS:
        base_score = base_scores.get(name, 0)
        tailored_score = tailored_scores.get(name, 0)
        delta = tailored_score - base_score
        rows.append((name, str(base_score), str(tailored_score), _format_delta(delta)))

    total_delta = tailored_report.overall_score - base_report.overall_score
    rows.append(
        (
            "Total",
            str(base_report.overall_score),
            str(tailored_report.overall_score),
            _format_delta(total_delta),
        )
    )

    headers = ("Dimension", "Base", "Tailored", "Delta")
    widths = [
        max(len(headers[0]), *(len(row[0]) for row in rows)),
        max(len(headers[1]), *(len(row[1]) for row in rows)),
        max(len(headers[2]), *(len(row[2]) for row in rows)),
        max(len(headers[3]), *(len(row[3]) for row in rows)),
    ]
    lines = [
        "Resume Match Comparison:",
        f"Scores are /10 per dimension and /{MATCH_MAX_SCORE} total.",
        _format_table_row(headers, widths),
        _format_table_separator(widths),
    ]
    lines.extend(_format_table_row(row, widths) for row in rows)
    return "\n".join(lines)


def _scores_by_dimension(report: ResumeMatchReport) -> dict[str, int]:
    return {dimension.name: dimension.score for dimension in report.dimension_scores}


def _format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _format_table_row(values: tuple[str, str, str, str], widths: list[int]) -> str:
    return (
        f"{values[0]:<{widths[0]}}  "
        f"{values[1]:>{widths[1]}}  "
        f"{values[2]:>{widths[2]}}  "
        f"{values[3]:>{widths[3]}}"
    )


def _format_table_separator(widths: list[int]) -> str:
    return "  ".join("-" * width for width in widths)


def _error_summary_row(*, resume_path: Path, error: str) -> dict[str, str]:
    return _base_summary_row(resume_path=resume_path, status="error", error=error)


def _base_summary_row(*, resume_path: Path, status: str, error: str) -> dict[str, str]:
    row = {header: "" for header in _summary_headers()}
    row["resume_file"] = resume_path.name
    row["resume_path"] = str(resume_path)
    row["status"] = status
    row["error"] = error
    return row


def _summary_headers() -> list[str]:
    return [
        "resume_file",
        "resume_path",
        "status",
        "error",
        "overall_score",
        "rating_label",
        *[_dimension_column_name(name) for name, _ in MATCH_DIMENSIONS],
        "summary",
    ]


def _summary_sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    if row["status"] != "success":
        return (1, 0, row["resume_file"].lower())

    score = int(row["overall_score"] or 0)
    return (0, -score, row["resume_file"].lower())


def _dimension_column_name(name: str) -> str:
    chars: list[str] = []
    last_was_separator = False
    for char in name.lower():
        if char.isalnum():
            chars.append(char)
            last_was_separator = False
            continue

        if chars and not last_was_separator:
            chars.append("_")
            last_was_separator = True

    return "".join(chars).strip("_")


def _format_list(label: str, values: list[str]) -> list[str]:
    if not values:
        return [f"- {label}: none identified"]

    joined_values = "; ".join(value.strip() for value in values if value.strip())
    return [f"- {label}: {joined_values or 'none identified'}"]


def _format_bullets(values: list[str]) -> list[str]:
    bullets = [f"- {value.strip()}" for value in values if value.strip()]
    return bullets or ["- None identified"]


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
