import tempfile
import unittest
from pathlib import Path

from experiment_config import (
    ExperimentConfigError,
    load_experiment_config,
    validate_experiment_inputs,
)


class ExperimentConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "jobs" / "nested").mkdir(parents=True)
        (self.root / "jobs" / "nested" / "role.txt").write_text(
            "Job description", encoding="utf-8"
        )
        (self.root / "jobs" / "ignored.md").write_text("Ignored", encoding="utf-8")
        (self.root / "context_a").mkdir()
        (self.root / "context_b").mkdir()
        (self.root / "template.docx").write_bytes(b"placeholder")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_config(self, treatment_ids: tuple[str, str] = ("a", "b")) -> Path:
        path = self.root / "experiment.yaml"
        path.write_text(
            f"""experiment_id: test_experiment
job_descriptions_dir: jobs
resume_template: template.docx
artifact_root: runs
generation_replicates: 1
treatments:
  - id: {treatment_ids[0]}
    model: gpt-5.6-terra
    career_history_dir: context_a
  - id: {treatment_ids[1]}
    model: gpt-5.6-terra
    career_history_dir: context_b
evaluation:
  judges:
    - id: judge
      provider: openai
      model: gpt-5.6-terra
""",
            encoding="utf-8",
        )
        return path

    def test_resolves_paths_and_discovers_only_txt_jobs(self) -> None:
        config = load_experiment_config(self.write_config())
        jobs = validate_experiment_inputs(config)

        self.assertEqual(config.job_descriptions_dir, (self.root / "jobs").resolve())
        self.assertEqual(config.artifact_root, (self.root / "runs").resolve())
        self.assertEqual(jobs, [(self.root / "jobs" / "nested" / "role.txt").resolve()])

    def test_rejects_duplicate_treatment_ids(self) -> None:
        with self.assertRaisesRegex(ExperimentConfigError, "Treatment IDs must be unique"):
            load_experiment_config(self.write_config(("same", "same")))

    def test_rejects_reserved_pair_separator(self) -> None:
        with self.assertRaisesRegex(ExperimentConfigError, "reserved separator"):
            load_experiment_config(self.write_config(("a__vs__b", "other")))

    def test_job_files_selects_only_named_descriptions(self) -> None:
        (self.root / "jobs" / "other.txt").write_text("Other", encoding="utf-8")
        path = self.write_config()
        content = path.read_text(encoding="utf-8").replace(
            "resume_template: template.docx",
            "job_files:\n  - nested/role.txt\nresume_template: template.docx",
        )
        path.write_text(content, encoding="utf-8")

        jobs = validate_experiment_inputs(load_experiment_config(path))

        self.assertEqual(jobs, [(self.root / "jobs" / "nested" / "role.txt").resolve()])

    def test_rejects_anthropic_temperature_override(self) -> None:
        path = self.write_config()
        content = path.read_text(encoding="utf-8").replace(
            "model: gpt-5.6-terra\n    career_history_dir: context_a",
            "provider: anthropic\n    model: claude-sonnet-5\n    temperature: 0\n"
            "    career_history_dir: context_a",
            1,
        )
        path.write_text(content, encoding="utf-8")

        with self.assertRaisesRegex(ExperimentConfigError, "temperature to null"):
            load_experiment_config(path)


if __name__ == "__main__":
    unittest.main()
