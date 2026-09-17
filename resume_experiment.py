"""Run reproducible, blinded treatment comparisons for tailored resumes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from career_history import load_career_history
from docx_editor import load_resume_template, write_tailored_resume
from experiment_config import (
    ExperimentConfig,
    ExperimentConfigError,
    JudgeConfig,
    TreatmentConfig,
    load_experiment_config,
    validate_experiment_inputs,
)
from experiment_generation import (
    compressor_for_treatment,
    estimate_call_cost,
    generate_for_treatment,
)
from experiment_judges import check_model_access, judge_pair, judge_resume
from experiment_prompts import ABSOLUTE_SYSTEM_PROMPT, DIMENSIONS, PAIRWISE_SYSTEM_PROMPT
from export_pdf import export_docx_to_pdf, inspect_resume_pdf_layout
from generation_metrics import ModelCallMetrics, normalize_usage
from prompts import COMPRESSION_SYSTEM_PROMPT, DRAFT_SYSTEM_PROMPT, FACT_CHECK_SYSTEM_PROMPT
from resume_reader import extract_resume_text
from tailor_resume import configure_logging, format_content_report, repair_pdf_overflow


LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and blindly compare resumes across experiment treatments."
    )
    parser.add_argument(
        "command",
        choices=("validate", "check-models", "generate", "evaluate", "report", "run"),
    )
    parser.add_argument(
        "--config",
        default="experiments/context_ab/experiment.yaml",
        help="Experiment YAML file.",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Existing run directory for generate/evaluate/report, or an explicit new directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate completed artifacts for the selected stage.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    run_dir: Path | None = None
    try:
        config_path = Path(args.config).expanduser().resolve()
        config = load_experiment_config(config_path)
        jobs = validate_experiment_inputs(config)

        if args.command == "validate":
            print_validation_summary(config=config, jobs=jobs)
            return 0
        if args.command == "check-models":
            check_all_models(config)
            print("All configured generation and judge models are accessible.")
            return 0
        if args.command in {"evaluate", "report"} and not args.run_dir:
            raise ExperimentConfigError(
                f"The {args.command!r} command requires --run-dir from an existing experiment run."
            )

        run_dir = resolve_run_dir(config=config, requested=args.run_dir)
        prepare_run(
            config=config,
            config_path=config_path,
            jobs=jobs,
            run_dir=run_dir,
        )
        if args.command in {"generate", "run"}:
            generate_all(config=config, jobs=jobs, run_dir=run_dir, force=args.force)
        if args.command in {"evaluate", "run"}:
            evaluate_all(config=config, jobs=jobs, run_dir=run_dir, force=args.force)
        if args.command in {"report", "run"}:
            write_reports(config=config, run_dir=run_dir)
        print(f"Experiment run: {run_dir}")
        return 0
    except Exception as exc:
        LOGGER.error("%s", exc)
        if run_dir is not None:
            LOGGER.error("Experiment run retained at: %s", run_dir)
        return 1


def resolve_run_dir(*, config: ExperimentConfig, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (config.artifact_root / f"{config.experiment_id}_{timestamp}").resolve()


def prepare_run(
    *,
    config: ExperimentConfig,
    config_path: Path,
    jobs: list[Path],
    run_dir: Path,
) -> None:
    """Create or validate a run manifest and immutable input snapshots."""

    manifest_path = run_dir / "manifest.json"
    config_sha256 = sha256_file(config_path)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("config_sha256") != config_sha256:
            raise ExperimentConfigError(
                "The requested run directory was created from a different config file."
            )
        current_jobs = [
            {
                "job_key": job_key_for_path(path.relative_to(config.job_descriptions_dir)),
                "relative_path": path.relative_to(config.job_descriptions_dir).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in jobs
        ]
        if manifest.get("jobs") != current_jobs:
            raise ExperimentConfigError(
                "Job-description inputs changed after this run was created. Start a new run."
            )
        if manifest.get("resume_template", {}).get("sha256") != sha256_file(
            config.resume_template
        ):
            raise ExperimentConfigError(
                "The resume template changed after this run was created. Start a new run."
            )
        recorded_treatments = {
            treatment["id"]: treatment for treatment in manifest.get("treatments", [])
        }
        for treatment in config.treatments:
            recorded = recorded_treatments.get(treatment.id, {})
            current_hash = sha256_directory(treatment.career_history_dir, pattern="*.md")
            if recorded.get("career_history_sha256") != current_hash:
                raise ExperimentConfigError(
                    f"Career-history inputs for {treatment.id!r} changed after this run was "
                    "created. Start a new run."
                )
        return

    run_dir.mkdir(parents=True, exist_ok=False)
    snapshots_dir = run_dir / "inputs" / "jobs"
    job_records = []
    seen_keys: set[str] = set()
    for job_path in jobs:
        relative_path = job_path.relative_to(config.job_descriptions_dir)
        job_key = job_key_for_path(relative_path)
        if job_key in seen_keys:
            raise ExperimentConfigError(
                f"Job filenames produce a duplicate experiment key: {job_key}"
            )
        seen_keys.add(job_key)
        snapshot_path = snapshots_dir / relative_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(job_path.read_text(encoding="utf-8"), encoding="utf-8")
        job_records.append(
            {
                "job_key": job_key,
                "relative_path": relative_path.as_posix(),
                "sha256": sha256_file(job_path),
            }
        )

    manifest = {
        "experiment_id": config.experiment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "config_sha256": config_sha256,
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_dirty": bool(_git_value("status", "--porcelain")),
        "prompt_sha256s": {
            "draft_system": sha256_text(DRAFT_SYSTEM_PROMPT),
            "fact_check_system": sha256_text(FACT_CHECK_SYSTEM_PROMPT),
            "compression_system": sha256_text(COMPRESSION_SYSTEM_PROMPT),
            "absolute_system": sha256_text(ABSOLUTE_SYSTEM_PROMPT),
            "pairwise_system": sha256_text(PAIRWISE_SYSTEM_PROMPT),
        },
        "jobs": job_records,
        "resume_template": {
            "path": str(config.resume_template),
            "sha256": sha256_file(config.resume_template),
        },
        "treatments": [
            {
                **treatment.model_dump(mode="json"),
                "career_history_sha256": sha256_directory(
                    treatment.career_history_dir, pattern="*.md"
                ),
            }
            for treatment in config.treatments
        ],
        "evaluation": config.evaluation.model_dump(mode="json"),
        "generation_replicates": config.generation_replicates,
    }
    write_json(manifest_path, manifest)


def generate_all(
    *, config: ExperimentConfig, jobs: list[Path], run_dir: Path, force: bool
) -> None:
    """Generate every Jobs x Treatments x Replicates artifact serially."""

    template = load_resume_template(config.resume_template)
    histories = {
        treatment.id: load_career_history(treatment.career_history_dir)
        for treatment in config.treatments
    }
    for job_path in jobs:
        relative_path = job_path.relative_to(config.job_descriptions_dir)
        job_key = job_key_for_path(relative_path)
        job_description = (
            run_dir / "inputs" / "jobs" / relative_path
        ).read_text(encoding="utf-8").strip()
        if not job_description:
            raise ExperimentConfigError(f"Job description is empty: {job_path}")
        for treatment in config.treatments:
            for replicate in range(1, config.generation_replicates + 1):
                artifact_dir = generation_dir(
                    run_dir=run_dir,
                    job_key=job_key,
                    treatment_id=treatment.id,
                    replicate=replicate,
                )
                completion_path = artifact_dir / "complete.json"
                if completion_path.exists() and not force:
                    LOGGER.info("Skipping completed generation %s", artifact_dir)
                    continue
                LOGGER.info(
                    "Generating %s / %s / replicate %02d",
                    job_key,
                    treatment.id,
                    replicate,
                )
                generate_one(
                    treatment=treatment,
                    career_history=histories[treatment.id],
                    job_description=job_description,
                    job_relative_path=relative_path,
                    replicate=replicate,
                    template=template,
                    artifact_dir=artifact_dir,
                )


def generate_one(
    *,
    treatment: TreatmentConfig,
    career_history: Any,
    job_description: str,
    job_relative_path: Path,
    replicate: int,
    template: Any,
    artifact_dir: Path,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trace = generate_for_treatment(
        treatment=treatment,
        career_history_context=career_history.model_context(),
        allowed_sources_by_role=career_history.allowed_sources_by_role(),
        job_description=job_description,
    )
    calls = list(trace.calls)
    write_json(artifact_dir / "draft.json", trace.draft.model_dump())
    write_json(artifact_dir / "audited.json", trace.audited.model_dump())
    write_json(artifact_dir / "pre_compression.json", trace.tailoring.model_dump())

    docx_path = artifact_dir / "resume.docx"
    pdf_path = artifact_dir / "resume.pdf"
    write_tailored_resume(template=template, tailoring=trace.tailoring, output_path=docx_path)
    export_docx_to_pdf(docx_path, pdf_path)
    final_tailoring = repair_pdf_overflow(
        template=template,
        docx_output_path=docx_path,
        pdf_output_path=pdf_path,
        tailoring=trace.tailoring,
        model=treatment.model,
        temperature=treatment.temperature,
        reasoning_effort=treatment.reasoning_effort,
        compressor=compressor_for_treatment(treatment=treatment, calls=calls),
    )
    write_json(artifact_dir / "final.json", final_tailoring.model_dump())
    extracted = extract_resume_text(pdf_path)
    (artifact_dir / "extracted.txt").write_text(extracted.text + "\n", encoding="utf-8")
    (artifact_dir / "diagnostics.txt").write_text(
        format_content_report(final_tailoring) + "\n", encoding="utf-8"
    )
    layout = inspect_resume_pdf_layout(pdf_path)
    write_json(
        artifact_dir / "complete.json",
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "job_relative_path": job_relative_path.as_posix(),
            "treatment_id": treatment.id,
            "generation_provider": treatment.provider,
            "generation_model": treatment.model,
            "replicate": replicate,
            "page_count": layout.page_count,
            "extracted_word_count": len(extracted.text.split()),
            "extraction_warnings": extracted.extraction_warnings,
            "model_calls": [call.model_dump() for call in calls],
            "model_call_count": len(calls),
            "model_duration_seconds": round(sum(call.duration_seconds for call in calls), 3),
            "estimated_cost_usd": estimate_call_cost(
                calls=calls,
                pricing=treatment.pricing,
            ),
        },
    )


def evaluate_all(
    *, config: ExperimentConfig, jobs: list[Path], run_dir: Path, force: bool
) -> None:
    """Run the configured absolute and pairwise evaluation stages."""

    if config.evaluation.absolute_scoring:
        evaluate_absolute_all(config=config, jobs=jobs, run_dir=run_dir, force=force)
    if config.evaluation.pairwise_scoring:
        evaluate_pairwise_all(config=config, jobs=jobs, run_dir=run_dir, force=force)


def evaluate_absolute_all(
    *, config: ExperimentConfig, jobs: list[Path], run_dir: Path, force: bool
) -> None:
    """Score each generated resume independently against its job description."""

    for job_path in jobs:
        relative_path = job_path.relative_to(config.job_descriptions_dir)
        job_key = job_key_for_path(relative_path)
        job_description = (
            run_dir / "inputs" / "jobs" / relative_path
        ).read_text(encoding="utf-8").strip()
        for treatment in config.treatments:
            for replicate in range(1, config.generation_replicates + 1):
                resume = read_completed_resume(run_dir, job_key, treatment.id, replicate)
                for judge in config.evaluation.judges:
                    for repetition in range(
                        1, config.evaluation.repetitions_per_order + 1
                    ):
                        output_path = (
                            run_dir
                            / "absolute_evaluations"
                            / job_key
                            / treatment.id
                            / f"replicate_{replicate:02d}"
                            / judge.id
                            / f"score_{repetition:02d}.json"
                        )
                        if output_path.exists() and not force:
                            LOGGER.info("Skipping completed absolute evaluation %s", output_path)
                            continue
                        LOGGER.info(
                            "Scoring %s / %s / replicate %02d / %s",
                            job_key,
                            treatment.id,
                            replicate,
                            judge.id,
                        )
                        response = judge_resume(
                            judge=judge,
                            job_description=job_description,
                            resume=resume,
                        )
                        write_json(
                            output_path,
                            {
                                "job_key": job_key,
                                "treatment_id": treatment.id,
                                "generation_replicate": replicate,
                                "judge_id": judge.id,
                                "judge_provider": judge.provider,
                                "judge_model": judge.model,
                                "repetition": repetition,
                                "evaluation": response.evaluation.model_dump(),
                                "usage": response.usage,
                                "duration_seconds": response.duration_seconds,
                                "estimated_cost_usd": estimate_judge_cost(
                                    usage=response.usage,
                                    duration_seconds=response.duration_seconds,
                                    judge=judge,
                                    stage="absolute evaluation",
                                ),
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )


def evaluate_pairwise_all(
    *, config: ExperimentConfig, jobs: list[Path], run_dir: Path, force: bool
) -> None:
    """Evaluate every treatment pair with blinded labels and reversed order."""

    pairs = list(itertools.combinations(config.treatments, 2))
    order_names = ("forward", "reverse") if config.evaluation.evaluate_both_orders else ("forward",)
    for job_path in jobs:
        relative_path = job_path.relative_to(config.job_descriptions_dir)
        job_key = job_key_for_path(relative_path)
        job_description = (
            run_dir / "inputs" / "jobs" / relative_path
        ).read_text(encoding="utf-8").strip()
        for left, right in pairs:
            pair_id = f"{left.id}__vs__{right.id}"
            for replicate in range(1, config.generation_replicates + 1):
                left_text = read_completed_resume(run_dir, job_key, left.id, replicate)
                right_text = read_completed_resume(run_dir, job_key, right.id, replicate)
                for judge in config.evaluation.judges:
                    for order_name in order_names:
                        x_id, y_id = (
                            (left.id, right.id)
                            if order_name == "forward"
                            else (right.id, left.id)
                        )
                        resume_x, resume_y = (
                            (left_text, right_text)
                            if order_name == "forward"
                            else (right_text, left_text)
                        )
                        for repetition in range(
                            1, config.evaluation.repetitions_per_order + 1
                        ):
                            output_path = (
                                run_dir
                                / "evaluations"
                                / job_key
                                / pair_id
                                / f"replicate_{replicate:02d}"
                                / judge.id
                                / f"{order_name}_{repetition:02d}.json"
                            )
                            if output_path.exists() and not force:
                                LOGGER.info("Skipping completed evaluation %s", output_path)
                                continue
                            LOGGER.info(
                                "Evaluating %s / %s / %s / %s",
                                job_key,
                                pair_id,
                                judge.id,
                                order_name,
                            )
                            response = judge_pair(
                                judge=judge,
                                job_description=job_description,
                                resume_x=resume_x,
                                resume_y=resume_y,
                            )
                            canonical_winner = canonicalize_winner(
                                response.comparison.winner, x_id=x_id, y_id=y_id
                            )
                            write_json(
                                output_path,
                                {
                                    "job_key": job_key,
                                    "pair_id": pair_id,
                                    "generation_replicate": replicate,
                                    "judge_id": judge.id,
                                    "judge_provider": judge.provider,
                                    "judge_model": judge.model,
                                    "order": order_name,
                                    "repetition": repetition,
                                    "resume_x_treatment": x_id,
                                    "resume_y_treatment": y_id,
                                    "canonical_winner": canonical_winner,
                                    "comparison": response.comparison.model_dump(),
                                    "usage": response.usage,
                                    "duration_seconds": response.duration_seconds,
                                    "estimated_cost_usd": estimate_judge_cost(
                                        usage=response.usage,
                                        duration_seconds=response.duration_seconds,
                                        judge=judge,
                                        stage="pairwise evaluation",
                                    ),
                                    "completed_at": datetime.now(timezone.utc).isoformat(),
                                },
                            )


def write_reports(*, config: ExperimentConfig, run_dir: Path) -> None:
    """Aggregate raw judgments without treating repeated calls as independent jobs."""

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    job_count = len(manifest["jobs"])
    pair_count = len(list(itertools.combinations(config.treatments, 2)))
    order_count = 2 if config.evaluation.evaluate_both_orders else 1
    pairwise_paths = sorted((run_dir / "evaluations").rglob("*.json"))
    absolute_paths = sorted((run_dir / "absolute_evaluations").rglob("*.json"))
    expected_pairwise = (
        job_count
        * pair_count
        * config.generation_replicates
        * len(config.evaluation.judges)
        * order_count
        * config.evaluation.repetitions_per_order
        if config.evaluation.pairwise_scoring
        else 0
    )
    expected_absolute = (
        job_count
        * len(config.treatments)
        * config.generation_replicates
        * len(config.evaluation.judges)
        * config.evaluation.repetitions_per_order
        if config.evaluation.absolute_scoring
        else 0
    )
    if len(pairwise_paths) != expected_pairwise:
        raise ExperimentConfigError(
            f"Pairwise evaluation is incomplete: found {len(pairwise_paths)} of "
            f"{expected_pairwise} expected judgments. Resume the evaluate stage first."
        )
    if len(absolute_paths) != expected_absolute:
        raise ExperimentConfigError(
            f"Absolute evaluation is incomplete: found {len(absolute_paths)} of "
            f"{expected_absolute} expected judgments. Resume the evaluate stage first."
        )
    pairwise_records = [
        json.loads(path.read_text(encoding="utf-8")) for path in pairwise_paths
    ]
    absolute_records = [
        json.loads(path.read_text(encoding="utf-8")) for path in absolute_paths
    ]
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    job_results: list[dict[str, Any]] = []
    overall_by_pair: dict[str, Counter[str]] = defaultdict(Counter)
    judgments_by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    judgments_by_judge: dict[str, Counter[str]] = defaultdict(Counter)
    order_flip_count = 0
    order_comparison_count = 0
    if pairwise_records:
        rows = build_score_rows(pairwise_records)
        with (reports_dir / "judgments.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in pairwise_records:
            grouped[(record["job_key"], record["pair_id"])].append(record)
        for (job_key, pair_id), group in sorted(grouped.items()):
            votes = Counter(record["canonical_winner"] for record in group)
            treatment_ids = pair_id.split("__vs__")
            winner = majority_winner(votes=votes, treatment_ids=treatment_ids)
            result = {
                "job_key": job_key,
                "pair_id": pair_id,
                "winner": winner,
                "votes": dict(votes),
                "judgment_count": len(group),
                "dimension_mean_differences": aggregate_dimension_differences(
                    group, treatment_ids
                ),
            }
            job_results.append(result)
            overall_by_pair[pair_id][winner] += 1
        for record in pairwise_records:
            judgments_by_pair[record["pair_id"]].append(record)
            judgments_by_judge[record["judge_id"]][record["canonical_winner"]] += 1
        order_flip_count, order_comparison_count = count_order_flips(pairwise_records)

    absolute_summary: dict[str, Any] | None = None
    if absolute_records:
        absolute_rows = build_absolute_score_rows(absolute_records)
        with (reports_dir / "absolute_scores.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(absolute_rows[0]))
            writer.writeheader()
            writer.writerows(absolute_rows)
        absolute_summary = build_absolute_summary(absolute_rows)

    generation_completions = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "generated").rglob("complete.json"))
    ]
    generation_costs = [
        item["estimated_cost_usd"]
        for item in generation_completions
        if item.get("estimated_cost_usd") is not None
    ]
    evaluation_costs = [
        item["estimated_cost_usd"]
        for item in [*pairwise_records, *absolute_records]
        if item.get("estimated_cost_usd") is not None
    ]
    summary = {
        "experiment_id": config.experiment_id,
        "job_count": job_count,
        "generation_count": len(generation_completions),
        "judgment_count": len(pairwise_records) + len(absolute_records),
        "absolute_judgment_count": len(absolute_records),
        "pairwise_judgment_count": len(pairwise_records),
        "absolute_scoring": absolute_summary,
        "job_results": job_results,
        "job_level_results_by_pair": {
            pair_id: dict(counts) for pair_id, counts in overall_by_pair.items()
        },
        "mean_dimension_differences_by_pair": {
            pair_id: aggregate_dimension_differences(
                group, pair_id.split("__vs__")
            )
            for pair_id, group in judgments_by_pair.items()
        },
        "raw_winners_by_judge": {
            judge_id: dict(counts) for judge_id, counts in judgments_by_judge.items()
        },
        "order_sensitivity": {
            "flips": order_flip_count,
            "comparisons": order_comparison_count,
            "flip_rate": (
                round(order_flip_count / order_comparison_count, 4)
                if order_comparison_count
                else None
            ),
        },
        "estimated_cost_usd": {
            "generation": round(sum(generation_costs), 6) if generation_costs else None,
            "evaluation": round(sum(evaluation_costs), 6) if evaluation_costs else None,
            "total": (
                round(sum(generation_costs) + sum(evaluation_costs), 6)
                if generation_costs or evaluation_costs
                else None
            ),
        },
    }
    write_json(reports_dir / "summary.json", summary)
    (reports_dir / "summary.md").write_text(render_markdown_summary(summary), encoding="utf-8")


def build_absolute_score_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten independent resume scores for CSV export and aggregation."""

    rows: list[dict[str, Any]] = []
    for record in records:
        dimensions = record["evaluation"]["dimension_scores"]
        scores = {dimension: int(dimensions[dimension]["score"]) for dimension in DIMENSIONS}
        row: dict[str, Any] = {
            "job_key": record["job_key"],
            "treatment_id": record["treatment_id"],
            "generation_replicate": record["generation_replicate"],
            "judge_id": record["judge_id"],
            "judge_model": record["judge_model"],
            "repetition": record["repetition"],
            "mean_score": round(mean(scores.values()), 3),
            "confidence": int(record["evaluation"]["confidence"]),
        }
        row.update(scores)
        rows.append(row)
    return rows


def build_absolute_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate absolute scores by treatment and by job without call-count inflation."""

    by_treatment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_job_treatment: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_job_treatment_replicate: dict[tuple[str, str, int], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    by_judge_treatment: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_treatment[row["treatment_id"]].append(row)
        by_job_treatment[(row["job_key"], row["treatment_id"])].append(row)
        by_job_treatment_replicate[
            (row["job_key"], row["treatment_id"], row["generation_replicate"])
        ].append(row)
        by_judge_treatment[(row["judge_id"], row["treatment_id"])].append(row)

    replicate_means = {
        key: mean(row["mean_score"] for row in group)
        for key, group in by_job_treatment_replicate.items()
    }

    treatment_results = {}
    for treatment_id, group in sorted(by_treatment.items()):
        within_job_stdevs = []
        for job_key in {row["job_key"] for row in group}:
            values = [
                score
                for (candidate_job, candidate_treatment, _), score in replicate_means.items()
                if candidate_job == job_key and candidate_treatment == treatment_id
            ]
            if values:
                within_job_stdevs.append(pstdev(values))
        treatment_results[treatment_id] = {
            "mean_score": round(mean(row["mean_score"] for row in group), 3),
            "median_score": round(median(row["mean_score"] for row in group), 3),
            "mean_dimensions": {
                dimension: round(mean(row[dimension] for row in group), 3)
                for dimension in DIMENSIONS
            },
            "judgment_count": len(group),
            "mean_within_job_generation_stdev": round(mean(within_job_stdevs), 3),
        }

    jobs: dict[str, dict[str, Any]] = defaultdict(dict)
    for (job_key, treatment_id), group in sorted(by_job_treatment.items()):
        generation_scores = sorted(
            score
            for (candidate_job, candidate_treatment, _), score in replicate_means.items()
            if candidate_job == job_key and candidate_treatment == treatment_id
        )
        jobs[job_key][treatment_id] = {
            "mean_score": round(mean(row["mean_score"] for row in group), 3),
            "median_score": round(median(row["mean_score"] for row in group), 3),
            "judgment_count": len(group),
            "generation_mean_scores": [round(value, 3) for value in generation_scores],
            "generation_stdev": round(pstdev(generation_scores), 3),
        }
    judge_results: dict[str, dict[str, float]] = defaultdict(dict)
    for (judge_id, treatment_id), group in sorted(by_judge_treatment.items()):
        judge_results[judge_id][treatment_id] = round(
            mean(row["mean_score"] for row in group), 3
        )
    return {
        "by_treatment": treatment_results,
        "by_job": dict(jobs),
        "mean_score_by_judge_and_treatment": dict(judge_results),
    }


def build_score_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        scores = {
            score["dimension"]: score for score in record["comparison"]["dimension_scores"]
        }
        row: dict[str, Any] = {
            "job_key": record["job_key"],
            "pair_id": record["pair_id"],
            "generation_replicate": record["generation_replicate"],
            "judge_id": record["judge_id"],
            "judge_model": record["judge_model"],
            "order": record["order"],
            "repetition": record["repetition"],
            "resume_x_treatment": record["resume_x_treatment"],
            "resume_y_treatment": record["resume_y_treatment"],
            "winner": record["canonical_winner"],
            "confidence": record["comparison"]["confidence"],
        }
        for dimension in DIMENSIONS:
            score = scores[dimension]
            row[f"{dimension}_x"] = score["resume_x"]
            row[f"{dimension}_y"] = score["resume_y"]
        rows.append(row)
    return rows


def aggregate_dimension_differences(
    records: list[dict[str, Any]], treatment_ids: list[str]
) -> dict[str, float]:
    left, right = treatment_ids
    values: dict[str, list[int]] = defaultdict(list)
    for record in records:
        for score in record["comparison"]["dimension_scores"]:
            by_treatment = {
                record["resume_x_treatment"]: score["resume_x"],
                record["resume_y_treatment"]: score["resume_y"],
            }
            values[score["dimension"]].append(by_treatment[right] - by_treatment[left])
    return {
        dimension: round(sum(items) / len(items), 3)
        for dimension, items in values.items()
    }


def majority_winner(*, votes: Counter[str], treatment_ids: list[str]) -> str:
    counts = {treatment_id: votes[treatment_id] for treatment_id in treatment_ids}
    highest = max(counts.values(), default=0)
    leaders = [key for key, value in counts.items() if value == highest]
    if len(leaders) != 1 or votes["tie"] >= highest:
        return "tie"
    return leaders[0]


def count_order_flips(records: list[dict[str, Any]]) -> tuple[int, int]:
    """Count matched judgments whose canonical winner changes after swapping X/Y."""

    grouped: dict[tuple[Any, ...], dict[str, str]] = defaultdict(dict)
    for record in records:
        key = (
            record["job_key"],
            record["pair_id"],
            record["generation_replicate"],
            record["judge_id"],
            record["repetition"],
        )
        grouped[key][record["order"]] = record["canonical_winner"]
    matched = [orders for orders in grouped.values() if {"forward", "reverse"} <= orders.keys()]
    flips = sum(orders["forward"] != orders["reverse"] for orders in matched)
    return flips, len(matched)


def render_markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"# Experiment: {summary['experiment_id']}",
        "",
        f"Jobs evaluated: {summary['job_count']}",
        f"Resumes generated: {summary.get('generation_count', 0)}",
        f"Raw judgments: {summary['judgment_count']}",
    ]
    absolute = summary.get("absolute_scoring")
    if absolute:
        lines.extend(["", "## Independent absolute scoring", ""])
        for treatment_id, result in absolute["by_treatment"].items():
            lines.append(
                f"- `{treatment_id}`: mean {result['mean_score']:.3f}, "
                f"median {result['median_score']:.3f} "
                f"({result['judgment_count']} judgments); mean within-job generation "
                f"SD {result['mean_within_job_generation_stdev']:.3f}"
            )
        lines.extend(["", "### Absolute scores by job", ""])
        for job_key, treatments in absolute["by_job"].items():
            formatted = ", ".join(
                f"`{treatment_id}` {result['mean_score']:.3f} "
                f"(generation SD {result['generation_stdev']:.3f})"
                for treatment_id, result in treatments.items()
            )
            lines.append(f"- `{job_key}`: {formatted}")

    if summary.get("pairwise_judgment_count") or summary.get("job_results"):
        lines.extend(["", "## Pairwise job-level results", ""])
    for pair_id, counts in summary["job_level_results_by_pair"].items():
        formatted = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        lines.append(f"- `{pair_id}`: {formatted}")
    sensitivity = summary["order_sensitivity"]
    if sensitivity["comparisons"]:
        lines.extend(
            [
                "",
                "## Order sensitivity",
                "",
                f"Winner changed in {sensitivity['flips']} of "
                f"{sensitivity['comparisons']} matched order swaps "
                f"({sensitivity['flip_rate']:.1%}).",
            ]
        )
    if summary["job_results"]:
        lines.extend(["", "## Pairwise winners by job", ""])
        for result in summary["job_results"]:
            lines.append(
                f"- `{result['job_key']}` / `{result['pair_id']}`: **{result['winner']}** "
                f"({result['judgment_count']} judgments)"
            )
    costs = summary.get("estimated_cost_usd", {})
    if costs.get("total") is not None:
        lines.extend(
            [
                "",
                "## Estimated API cost",
                "",
                f"- Generation: ${costs['generation'] or 0:.4f}",
                f"- Evaluation: ${costs['evaluation'] or 0:.4f}",
                f"- Total: ${costs['total']:.4f}",
            ]
        )
    return "\n".join(lines) + "\n"


def canonicalize_winner(winner: str, *, x_id: str, y_id: str) -> str:
    if winner == "X":
        return x_id
    if winner == "Y":
        return y_id
    return "tie"


def estimate_judge_cost(
    *,
    usage: dict[str, Any],
    duration_seconds: float,
    judge: JudgeConfig,
    stage: str,
) -> float | None:
    """Normalize one judge response and estimate its configured list-price cost."""

    input_tokens, cached_input_tokens, output_tokens = normalize_usage(usage)
    call = ModelCallMetrics(
        stage=stage,
        provider=judge.provider,
        model=judge.model,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )
    return estimate_call_cost(calls=[call], pricing=judge.pricing)


def read_completed_resume(
    run_dir: Path, job_key: str, treatment_id: str, replicate: int
) -> str:
    artifact_dir = generation_dir(
        run_dir=run_dir,
        job_key=job_key,
        treatment_id=treatment_id,
        replicate=replicate,
    )
    if not (artifact_dir / "complete.json").is_file():
        raise ExperimentConfigError(f"Generation is incomplete: {artifact_dir}")
    return (artifact_dir / "extracted.txt").read_text(encoding="utf-8").strip()


def generation_dir(
    *, run_dir: Path, job_key: str, treatment_id: str, replicate: int
) -> Path:
    return (
        run_dir
        / "generated"
        / job_key
        / treatment_id
        / f"replicate_{replicate:02d}"
    )


def job_key_for_path(relative_path: Path) -> str:
    source = relative_path.with_suffix("").as_posix().lower()
    characters = [char if char.isalnum() else "_" for char in source]
    return "_".join(filter(None, "".join(characters).split("_"))) or "job"


def check_all_models(config: ExperimentConfig) -> None:
    checked: set[tuple[str, str]] = set()
    failures: list[str] = []
    for treatment in config.treatments:
        key = (treatment.provider, treatment.model)
        if key in checked:
            continue
        try:
            check_model_access(
                JudgeConfig(
                    id=f"generation_{treatment.id}",
                    provider=treatment.provider,
                    model=treatment.model,
                )
            )
            LOGGER.info("Confirmed access to %s model %s", key[0], key[1])
        except Exception as exc:
            failures.append(f"{key[0]}:{key[1]}: {exc}")
        checked.add(key)
    for judge in config.evaluation.judges:
        key = (judge.provider, judge.model)
        if key not in checked:
            try:
                check_model_access(judge)
                LOGGER.info("Confirmed access to %s model %s", key[0], key[1])
            except Exception as exc:
                failures.append(f"{key[0]}:{key[1]}: {exc}")
            checked.add(key)
    if failures:
        raise ExperimentConfigError(
            "Model access check failed:\n- " + "\n- ".join(failures)
        )


def print_validation_summary(*, config: ExperimentConfig, jobs: list[Path]) -> None:
    comparison_count = len(list(itertools.combinations(config.treatments, 2)))
    order_count = 2 if config.evaluation.evaluate_both_orders else 1
    generation_count = len(jobs) * len(config.treatments) * config.generation_replicates
    pairwise_evaluation_count = (
        len(jobs)
        * comparison_count
        * config.generation_replicates
        * len(config.evaluation.judges)
        * order_count
        * config.evaluation.repetitions_per_order
        if config.evaluation.pairwise_scoring
        else 0
    )
    absolute_evaluation_count = (
        len(jobs)
        * len(config.treatments)
        * config.generation_replicates
        * len(config.evaluation.judges)
        * config.evaluation.repetitions_per_order
        if config.evaluation.absolute_scoring
        else 0
    )
    print(f"Experiment: {config.experiment_id}")
    print(f"Jobs: {len(jobs)}")
    print(f"Treatments: {len(config.treatments)}")
    print(f"Planned resume generations: {generation_count}")
    print(f"Planned absolute judge calls: {absolute_evaluation_count}")
    print(f"Planned pairwise judge calls: {pairwise_evaluation_count}")
    print(f"Planned total judge calls: {absolute_evaluation_count + pairwise_evaluation_count}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_directory(path: Path, *, pattern: str) -> str:
    digest = hashlib.sha256()
    for candidate in sorted(path.rglob(pattern)):
        if not candidate.is_file():
            continue
        digest.update(candidate.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(candidate)))
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


if __name__ == "__main__":
    sys.exit(main())
