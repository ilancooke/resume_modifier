import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from experiment_config import ExperimentConfig
from experiment_prompts import DIMENSIONS
from resume_experiment import (
    aggregate_dimension_differences,
    build_absolute_score_rows,
    build_absolute_summary,
    canonicalize_winner,
    count_order_flips,
    job_key_for_path,
    majority_winner,
    write_reports,
)


def record(*, order: str, winner: str, x: str, y: str) -> dict:
    return {
        "job_key": "role",
        "pair_id": "a__vs__b",
        "generation_replicate": 1,
        "judge_id": "judge",
        "judge_model": "gpt-5.6-terra",
        "repetition": 1,
        "order": order,
        "resume_x_treatment": x,
        "resume_y_treatment": y,
        "canonical_winner": winner,
        "comparison": {
            "confidence": 80,
            "dimension_scores": [
                {
                    "dimension": dimension,
                    "resume_x": 6 if x == "a" else 8,
                    "resume_y": 8 if y == "b" else 6,
                    "rationale": "comparison",
                }
                for dimension in DIMENSIONS
            ]
        },
    }


def absolute_record(*, treatment: str, score: int) -> dict:
    return {
        "job_key": "role",
        "treatment_id": treatment,
        "generation_replicate": 1,
        "judge_id": "judge",
        "judge_provider": "openai",
        "judge_model": "gpt-5.6-terra",
        "repetition": 1,
        "evaluation": {
            "confidence": 80,
            "dimension_scores": {
                dimension: {"score": score, "rationale": "reason"}
                for dimension in DIMENSIONS
            },
        },
        "estimated_cost_usd": 0.01,
    }


class ResumeExperimentTests(unittest.TestCase):
    def test_canonicalizes_blinded_winner(self) -> None:
        self.assertEqual(canonicalize_winner("X", x_id="b", y_id="a"), "b")
        self.assertEqual(canonicalize_winner("Y", x_id="b", y_id="a"), "a")
        self.assertEqual(canonicalize_winner("tie", x_id="b", y_id="a"), "tie")

    def test_detects_order_flip_after_canonicalization(self) -> None:
        records = [
            record(order="forward", winner="b", x="a", y="b"),
            record(order="reverse", winner="a", x="b", y="a"),
        ]
        self.assertEqual(count_order_flips(records), (1, 1))

    def test_dimension_difference_is_treatment_ordered(self) -> None:
        records = [
            record(order="forward", winner="b", x="a", y="b"),
            record(order="reverse", winner="b", x="b", y="a"),
        ]
        differences = aggregate_dimension_differences(records, ["a", "b"])
        self.assertEqual(differences["employer_alignment"], 2.0)

    def test_majority_winner_respects_ties(self) -> None:
        self.assertEqual(
            majority_winner(votes=Counter({"a": 6, "b": 3, "tie": 1}), treatment_ids=["a", "b"]),
            "a",
        )
        self.assertEqual(
            majority_winner(votes=Counter({"a": 4, "b": 4, "tie": 2}), treatment_ids=["a", "b"]),
            "tie",
        )

    def test_job_key_uses_relative_filename_not_posting_job_id(self) -> None:
        self.assertEqual(job_key_for_path(Path("Data Science/Senior Role.txt")), "data_science_senior_role")

    def test_absolute_scores_aggregate_by_treatment_and_job(self) -> None:
        records = []
        for treatment, score in (("a", 7), ("b", 9)):
            records.append(
                {
                    "job_key": "role",
                    "treatment_id": treatment,
                    "generation_replicate": 1,
                    "judge_id": "judge",
                    "judge_model": "model",
                    "repetition": 1,
                    "evaluation": {
                        "confidence": 80,
                        "dimension_scores": {
                            dimension: {"score": score, "rationale": "reason"}
                            for dimension in DIMENSIONS
                        },
                    },
                }
            )

        summary = build_absolute_summary(build_absolute_score_rows(records))

        self.assertEqual(summary["by_treatment"]["a"]["mean_score"], 7.0)
        self.assertEqual(summary["by_job"]["role"]["b"]["median_score"], 9.0)

    def test_writes_complete_job_level_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = ExperimentConfig.model_validate(
                {
                    "experiment_id": "test",
                    "job_descriptions_dir": root,
                    "resume_template": root / "template.docx",
                    "artifact_root": root / "runs",
                    "treatments": [
                        {
                            "id": "a",
                            "model": "gpt-5.6-terra",
                            "career_history_dir": root / "a",
                        },
                        {
                            "id": "b",
                            "model": "gpt-5.6-terra",
                            "career_history_dir": root / "b",
                        },
                    ],
                    "evaluation": {
                        "judges": [
                            {
                                "id": "judge",
                                "provider": "openai",
                                "model": "gpt-5.6-terra",
                            }
                        ]
                    },
                }
            )
            (root / "manifest.json").write_text(
                json.dumps({"jobs": [{"job_key": "role"}]}), encoding="utf-8"
            )
            evaluation_dir = root / "evaluations" / "role" / "a__vs__b"
            evaluation_dir.mkdir(parents=True)
            for item in (
                record(order="forward", winner="b", x="a", y="b"),
                record(order="reverse", winner="b", x="b", y="a"),
            ):
                item["pair_id"] = "a__vs__b"
                path = evaluation_dir / f"{item['order']}.json"
                path.write_text(json.dumps(item), encoding="utf-8")

            write_reports(config=config, run_dir=root)

            summary = json.loads(
                (root / "reports" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["job_level_results_by_pair"]["a__vs__b"], {"b": 1})
            self.assertEqual(summary["order_sensitivity"]["flip_rate"], 0.0)

    def test_writes_combined_absolute_pairwise_and_cost_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = ExperimentConfig.model_validate(
                {
                    "experiment_id": "test",
                    "job_descriptions_dir": root,
                    "resume_template": root / "template.docx",
                    "artifact_root": root / "runs",
                    "treatments": [
                        {"id": "a", "model": "model-a", "career_history_dir": root / "a"},
                        {"id": "b", "model": "model-b", "career_history_dir": root / "b"},
                    ],
                    "evaluation": {
                        "absolute_scoring": True,
                        "pairwise_scoring": True,
                        "judges": [
                            {"id": "judge", "provider": "openai", "model": "judge-model"}
                        ],
                    },
                }
            )
            (root / "manifest.json").write_text(
                json.dumps({"jobs": [{"job_key": "role"}]}), encoding="utf-8"
            )
            pair_dir = root / "evaluations" / "role" / "a__vs__b"
            pair_dir.mkdir(parents=True)
            for item in (
                record(order="forward", winner="b", x="a", y="b"),
                record(order="reverse", winner="b", x="b", y="a"),
            ):
                (pair_dir / f"{item['order']}.json").write_text(
                    json.dumps(item), encoding="utf-8"
                )
            for treatment, score in (("a", 7), ("b", 9)):
                path = root / "absolute_evaluations" / f"{treatment}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(absolute_record(treatment=treatment, score=score)),
                    encoding="utf-8",
                )
                completion = root / "generated" / treatment / "complete.json"
                completion.parent.mkdir(parents=True, exist_ok=True)
                completion.write_text(
                    json.dumps({"estimated_cost_usd": 0.02}), encoding="utf-8"
                )

            write_reports(config=config, run_dir=root)

            summary = json.loads(
                (root / "reports" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["absolute_judgment_count"], 2)
            self.assertEqual(summary["pairwise_judgment_count"], 2)
            self.assertEqual(
                summary["absolute_scoring"]["by_treatment"]["b"]["mean_score"], 9.0
            )
            self.assertEqual(summary["estimated_cost_usd"]["total"], 0.06)
            self.assertTrue((root / "reports" / "absolute_scores.csv").is_file())


if __name__ == "__main__":
    unittest.main()
