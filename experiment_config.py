"""Configuration models for reproducible resume experiments."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ExperimentConfigError(RuntimeError):
    """Raised when an experiment configuration cannot be loaded safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelPricing(StrictModel):
    """USD list prices used to estimate experiment cost from recorded usage."""

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)


class TreatmentConfig(StrictModel):
    """One complete resume-generation treatment."""

    id: str
    career_history_dir: Path
    model: str
    provider: Literal["openai", "anthropic"] = "openai"
    temperature: float | None = 0
    reasoning_effort: str | None = "none"
    anthropic_thinking: Literal["disabled", "adaptive"] | None = None
    max_output_tokens: int = Field(default=16000, ge=1000)
    pricing: ModelPricing | None = None
    prompt_version: Literal["current"] = "current"

    @model_validator(mode="after")
    def validate_provider_settings(self) -> "TreatmentConfig":
        if self.provider == "anthropic" and self.temperature is not None:
            raise ValueError(
                "Anthropic generation treatments must set temperature to null; "
                "Claude Sonnet 5 rejects non-default sampling parameters."
            )
        return self


class JudgeConfig(StrictModel):
    """One pairwise evaluator model."""

    id: str
    provider: Literal["openai", "anthropic"]
    model: str
    reasoning_effort: str | None = None
    temperature: float | None = None
    anthropic_thinking: Literal["disabled", "adaptive"] | None = "disabled"
    max_output_tokens: int = Field(default=5000, ge=500)
    pricing: ModelPricing | None = None


class EvaluationConfig(StrictModel):
    """Controls blinded pairwise evaluation repetitions."""

    judges: list[JudgeConfig] = Field(min_length=1)
    absolute_scoring: bool = False
    pairwise_scoring: bool = True
    evaluate_both_orders: bool = True
    repetitions_per_order: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def require_an_evaluation_mode(self) -> "EvaluationConfig":
        if not self.absolute_scoring and not self.pairwise_scoring:
            raise ValueError("Enable absolute_scoring, pairwise_scoring, or both.")
        return self


class ExperimentConfig(StrictModel):
    """Resolved configuration for a treatment-based resume experiment."""

    experiment_id: str
    job_descriptions_dir: Path
    job_files: list[str] | None = None
    resume_template: Path
    artifact_root: Path = Path("experiment_runs")
    generation_replicates: int = Field(default=1, ge=1)
    treatments: list[TreatmentConfig] = Field(min_length=2)
    evaluation: EvaluationConfig

    @model_validator(mode="after")
    def validate_identifiers(self) -> "ExperimentConfig":
        _validate_id(self.experiment_id, "experiment_id")
        treatment_ids = [treatment.id for treatment in self.treatments]
        judge_ids = [judge.id for judge in self.evaluation.judges]
        for value in treatment_ids:
            _validate_id(value, "treatment id")
        for value in judge_ids:
            _validate_id(value, "judge id")
        if len(set(treatment_ids)) != len(treatment_ids):
            raise ValueError("Treatment IDs must be unique.")
        if len(set(judge_ids)) != len(judge_ids):
            raise ValueError("Judge IDs must be unique.")
        return self


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    """Load YAML and resolve its paths relative to the configuration file."""

    config_path = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExperimentConfigError(f"Experiment config not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ExperimentConfigError(f"Experiment config is not valid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExperimentConfigError("Experiment config must contain a YAML mapping.")

    base_dir = config_path.parent
    for key in ("job_descriptions_dir", "resume_template", "artifact_root"):
        if key in payload:
            payload[key] = _resolve_path(payload[key], base_dir=base_dir)
    treatments = payload.get("treatments")
    if isinstance(treatments, list):
        for treatment in treatments:
            if isinstance(treatment, dict) and "career_history_dir" in treatment:
                treatment["career_history_dir"] = _resolve_path(
                    treatment["career_history_dir"], base_dir=base_dir
                )

    try:
        return ExperimentConfig.model_validate(payload)
    except Exception as exc:
        raise ExperimentConfigError(f"Invalid experiment config: {exc}") from exc


def validate_experiment_inputs(config: ExperimentConfig) -> list[Path]:
    """Validate local inputs and return sorted job-description files."""

    if not config.job_descriptions_dir.is_dir():
        raise ExperimentConfigError(
            f"Job-description directory not found: {config.job_descriptions_dir}"
        )
    if not config.resume_template.is_file():
        raise ExperimentConfigError(f"Resume template not found: {config.resume_template}")
    for treatment in config.treatments:
        if not treatment.career_history_dir.is_dir():
            raise ExperimentConfigError(
                f"Career-history directory for {treatment.id!r} not found: "
                f"{treatment.career_history_dir}"
            )

    if config.job_files is None:
        jobs = sorted(
            (path for path in config.job_descriptions_dir.rglob("*.txt") if path.is_file()),
            key=lambda path: path.relative_to(config.job_descriptions_dir).as_posix().lower(),
        )
    else:
        jobs = []
        seen: set[str] = set()
        for relative_value in config.job_files:
            relative_path = Path(relative_value)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ExperimentConfigError(
                    f"job_files entries must stay inside job_descriptions_dir: {relative_value!r}"
                )
            normalized = relative_path.as_posix()
            if normalized in seen:
                raise ExperimentConfigError(f"Duplicate job_files entry: {normalized}")
            seen.add(normalized)
            if relative_path.suffix.lower() != ".txt":
                raise ExperimentConfigError(
                    f"job_files entries must name .txt files: {normalized}"
                )
            job_path = (config.job_descriptions_dir / relative_path).resolve()
            if not job_path.is_file():
                raise ExperimentConfigError(f"Selected job description not found: {job_path}")
            jobs.append(job_path)
        jobs.sort(key=lambda path: path.relative_to(config.job_descriptions_dir).as_posix().lower())
    if not jobs:
        raise ExperimentConfigError(
            f"No .txt job descriptions found under: {config.job_descriptions_dir}"
        )
    return jobs


def _resolve_path(value: object, *, base_dir: Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise ExperimentConfigError(f"Expected a path string, got {value!r}.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _validate_id(value: str, label: str) -> None:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{label} {value!r} must use lowercase letters, digits, underscores, or hyphens."
        )
    if "__vs__" in value:
        raise ValueError(f"{label} {value!r} contains the reserved separator '__vs__'.")
