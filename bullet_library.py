"""Read approved resume accomplishments without generating or rewriting their text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ROLE_IDS = ("amazon_data_scientist", "amazon_ba_bie", "petsmart_analytics")
# These headings describe experience spanning projects rather than individual projects.
CROSS_PROJECT_GROUPS = {
    "MLOps and Operational Excellence",
    "Production Feature Engineering Examples",
}
BULLET_PATTERN = re.compile(r"^- \*\*([A-Z][A-Z0-9-]*):\*\*\s+(.+)$")


class BulletLibraryError(ValueError):
    """The library cannot safely be used for selection."""


@dataclass(frozen=True, slots=True)
class ApprovedBullet:
    bullet_id: str
    text: str
    role_id: str
    project: str
    source_file: str
    cross_project: bool

    @property
    def project_key(self) -> str:
        return f"{self.source_file}::{self.project}"


@dataclass(frozen=True, slots=True)
class BulletLibrary:
    root: Path
    bullets: tuple[ApprovedBullet, ...]

    def by_id(self) -> dict[str, ApprovedBullet]:
        return {bullet.bullet_id: bullet for bullet in self.bullets}

    def model_context(self) -> str:
        return json.dumps([
            {
                "id": bullet.bullet_id, "text": bullet.text, "role_id": bullet.role_id,
                "project": bullet.project_key, "cross_project": bullet.cross_project,
            }
            for bullet in self.bullets
        ], ensure_ascii=False, indent=2)

    def role_limits(self) -> dict[str, int]:
        counts = {role: sum(b.role_id == role for b in self.bullets) for role in ROLE_IDS}
        # The recent role can use the available space; earlier roles remain concise.
        counts["amazon_ba_bie"] = min(counts["amazon_ba_bie"], 2)
        counts["petsmart_analytics"] = min(counts["petsmart_analytics"], 4)
        return counts


def _role_for_path(relative: str) -> str:
    if relative.startswith("Amazon/Data Scientist/"):
        return ROLE_IDS[0]
    if relative.startswith("Amazon/BA-BIE/"):
        return ROLE_IDS[1]
    if relative == "Petsmart/PetSmart_Bullets.md":
        return ROLE_IDS[2]
    raise BulletLibraryError(f"Unrecognized bullet-library role path: {relative}")


def load_bullet_library(path: str | Path) -> BulletLibrary:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise BulletLibraryError(f"Bullet library folder not found: {root}")
    bullets: list[ApprovedBullet] = []
    seen: set[str] = set()
    for file in sorted(root.rglob("*.md")):
        relative = file.relative_to(root).as_posix()
        role_id = _role_for_path(relative)
        project = ""
        file_count = 0
        for line_number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            line = line.rstrip()
            if not line.strip() or line.startswith("# ") or line == "Candidate resume bullets.":
                continue
            if line.startswith("## "):
                project = line[3:].strip()
                continue
            match = BULLET_PATTERN.fullmatch(line)
            if not match or not project:
                raise BulletLibraryError(f"Malformed bullet or heading at {relative}:{line_number}")
            bullet_id, text = match.groups()
            prefixes = {ROLE_IDS[0]: ("ADS-",), ROLE_IDS[1]: ("ABA-",), ROLE_IDS[2]: ("PM-", "PC-")}
            if not bullet_id.startswith(prefixes[role_id]):
                raise BulletLibraryError(f"Bullet ID {bullet_id} does not belong to {role_id}")
            if bullet_id in seen:
                raise BulletLibraryError(f"Duplicate bullet ID: {bullet_id}")
            seen.add(bullet_id)
            bullets.append(ApprovedBullet(
                bullet_id, text, role_id, project, relative, project in CROSS_PROJECT_GROUPS,
            ))
            file_count += 1
        if not file_count:
            raise BulletLibraryError(f"No approved bullets in {relative}")
    missing = set(ROLE_IDS) - {bullet.role_id for bullet in bullets}
    if missing:
        raise BulletLibraryError("Missing bullet libraries for role(s): " + ", ".join(sorted(missing)))
    return BulletLibrary(root, tuple(bullets))
