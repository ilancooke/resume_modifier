"""Configuration helpers for resume output settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().with_name("config.json")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """User-editable application configuration."""

    resume_template_path: Path
    career_history_dir: Path
    job_description_path: Path
    tailored_resumes_dir: Path


class ConfigError(RuntimeError):
    """Raised when the local config file is missing or invalid."""


def load_config(path: Path | None = None) -> AppConfig:
    """Load application config from disk."""

    config_path = (path or CONFIG_PATH).expanduser().resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {config_path}") from exc

    resume_template_path = payload.get("resume_template_path", payload.get("base_resume_path"))
    if not isinstance(resume_template_path, str) or not resume_template_path.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'resume_template_path'."
        )

    career_history_dir = payload.get("career_history_dir")
    if not isinstance(career_history_dir, str) or not career_history_dir.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'career_history_dir'."
        )

    job_description_path = payload.get("job_description_path")
    if not isinstance(job_description_path, str) or not job_description_path.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'job_description_path'."
        )

    output_dir = payload.get("tailored_resumes_dir")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'tailored_resumes_dir'."
        )

    return AppConfig(
        resume_template_path=Path(resume_template_path).expanduser().resolve(),
        career_history_dir=Path(career_history_dir).expanduser().resolve(),
        job_description_path=Path(job_description_path).expanduser().resolve(),
        tailored_resumes_dir=Path(output_dir).expanduser().resolve(),
    )
