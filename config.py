"""Configuration helpers for resume output settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().with_name("config.json")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """User-editable application configuration."""

    base_resume_path: Path
    job_description_path: Path
    tailored_resumes_dir: Path
    job_application_tracker_path: Path


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

    base_resume_path = payload.get("base_resume_path")
    if not isinstance(base_resume_path, str) or not base_resume_path.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'base_resume_path'."
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

    tracker_path = payload.get("job_application_tracker_path")
    if not isinstance(tracker_path, str) or not tracker_path.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'job_application_tracker_path'."
        )

    return AppConfig(
        base_resume_path=Path(base_resume_path).expanduser().resolve(),
        job_description_path=Path(job_description_path).expanduser().resolve(),
        tailored_resumes_dir=Path(output_dir).expanduser().resolve(),
        job_application_tracker_path=Path(tracker_path).expanduser().resolve(),
    )
