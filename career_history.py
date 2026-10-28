"""Load and assign Markdown career-history evidence to resume roles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from resume_models import ROLE_SPECS

EXCLUDED_RELATIVE_PATHS = {"Template.md"}


class CareerHistoryError(RuntimeError):
    """Raised when career-history evidence cannot be assigned safely."""


@dataclass(frozen=True, slots=True)
class CareerHistoryFile:
    """One Markdown evidence file assigned to a resume role."""

    relative_path: str
    role_id: str
    text: str


@dataclass(frozen=True, slots=True)
class CareerHistory:
    """All included career-history evidence for one generation run."""

    root: Path
    files: tuple[CareerHistoryFile, ...]
    excluded_files: tuple[str, ...]

    def files_for_role(self, role_id: str) -> tuple[CareerHistoryFile, ...]:
        return tuple(file for file in self.files if file.role_id == role_id)

    def allowed_sources_by_role(self) -> dict[str, set[str]]:
        return {
            spec.role_id: {
                file.relative_path for file in self.files if file.role_id == spec.role_id
            }
            for spec in ROLE_SPECS
        }

    def all_source_paths(self) -> set[str]:
        return {file.relative_path for file in self.files}

    def model_context(self) -> str:
        """Format evidence with role and path boundaries while preserving Markdown."""

        sections: list[str] = []
        for spec in ROLE_SPECS:
            sections.append(
                f"# ROLE EVIDENCE: {spec.company} | {spec.role}\n"
                f"Role ID: {spec.role_id}"
            )
            for source in self.files_for_role(spec.role_id):
                sections.append(
                    f"## SOURCE FILE: {source.relative_path}\n\n{source.text.strip()}"
                )
        return "\n\n".join(sections)


def load_career_history(path: str | Path) -> CareerHistory:
    """Recursively read Markdown evidence and assign it from relative paths."""

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise CareerHistoryError(f"Career history folder not found: {root}")

    included: list[CareerHistoryFile] = []
    excluded: list[str] = []
    unknown: list[str] = []

    markdown_paths = sorted(
        (candidate for candidate in root.rglob("*.md") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix().lower(),
    )
    if not markdown_paths:
        raise CareerHistoryError(f"No Markdown files found in career history folder: {root}")

    for markdown_path in markdown_paths:
        relative_path = markdown_path.relative_to(root).as_posix()
        if relative_path in EXCLUDED_RELATIVE_PATHS:
            excluded.append(relative_path)
            continue

        role_id = _assign_role(relative_path)
        if role_id is None:
            unknown.append(relative_path)
            continue

        text = markdown_path.read_text(encoding="utf-8").strip()
        if not text:
            raise CareerHistoryError(f"Career history file is empty: {markdown_path}")
        included.append(
            CareerHistoryFile(
                relative_path=relative_path,
                role_id=role_id,
                text=text,
            )
        )

    if unknown:
        paths = ", ".join(unknown)
        raise CareerHistoryError(
            f"Could not assign Markdown file(s) to a resume role from their hierarchy: {paths}"
        )

    missing_roles = [
        spec.role_id
        for spec in ROLE_SPECS
        if not any(file.role_id == spec.role_id for file in included)
    ]
    if missing_roles:
        raise CareerHistoryError(
            "Career history is missing evidence for role(s): " + ", ".join(missing_roles)
        )

    return CareerHistory(
        root=root,
        files=tuple(included),
        excluded_files=tuple(excluded),
    )


def _assign_role(relative_path: str) -> str | None:
    for spec in ROLE_SPECS:
        for source_prefix in spec.source_prefixes:
            if source_prefix.endswith("/"):
                if relative_path.startswith(source_prefix):
                    return spec.role_id
            elif relative_path == source_prefix:
                return spec.role_id
    return None

