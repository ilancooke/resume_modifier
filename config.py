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
    career_history_dir: Path | None
    job_description_path: Path
    tailored_resumes_dir: Path
    resume_diagnostics_dir: Path
    bullet_library_dir: Path | None = None


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
    if career_history_dir is not None and (not isinstance(career_history_dir, str) or not career_history_dir.strip()):
        raise ConfigError(
            "Optional 'career_history_dir' must be a non-empty string."
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

    diagnostics_dir = payload.get("resume_diagnostics_dir")
    if not isinstance(diagnostics_dir, str) or not diagnostics_dir.strip():
        raise ConfigError(
            "Config file must define a non-empty string for 'resume_diagnostics_dir'."
        )

    bullet_library_dir = payload.get("bullet_library_dir")
    if not isinstance(bullet_library_dir, str) or not bullet_library_dir.strip():
        raise ConfigError("Config file must define a non-empty string for 'bullet_library_dir'.")

    return AppConfig(
        resume_template_path=Path(resume_template_path).expanduser().resolve(),
        career_history_dir=Path(career_history_dir).expanduser().resolve() if career_history_dir else None,
        job_description_path=Path(job_description_path).expanduser().resolve(),
        tailored_resumes_dir=Path(output_dir).expanduser().resolve(),
        resume_diagnostics_dir=Path(diagnostics_dir).expanduser().resolve(),
        bullet_library_dir=Path(bullet_library_dir).expanduser().resolve(),
    )
