"""Shared models and role requirements for generated resumes."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Structural guidance for one resume role block.

    Bullet-count guidance provides controlled flexibility:
    - preferred_bullets is a soft target when enough strong, distinct evidence exists.
    - max_bullets is a hard structural bound; roles may use fewer bullets.
    - max_words is a ceiling, not a target to fill.
    """

    role_id: str
    company: str
    role: str
    preferred_bullets: int
    max_bullets: int
    max_words: int
    source_prefix: str
    anchor_texts: tuple[str, ...]

    def valid_bullet_count(self, count: int) -> bool:
        """Return whether a generated bullet count is within the hard maximum."""
        return 0 <= count <= self.max_bullets


ROLE_SPECS = (
    RoleSpec(
        role_id="amazon_data_scientist",
        company="Amazon",
        role="Data Scientist, Marketplace Fraud & Abuse",
        preferred_bullets=7,
        max_bullets=8,
        max_words=275,
        source_prefix="Amazon/Data Scientist/",
        anchor_texts=("Data Scientist, Marketplace Fraud & Abuse",),
    ),
    RoleSpec(
        role_id="amazon_ba_bie",
        company="Amazon",
        role="Business Analyst / Business Intelligence Engineer",
        preferred_bullets=3,
        max_bullets=4,
        max_words=90,
        source_prefix="Amazon/BA-BIE/",
        anchor_texts=(
            "Business Intelligence Engineer",
            "Business Analyst",
            "Related Accounts Enforcement",
        ),
    ),
    RoleSpec(
        role_id="petsmart_manager_business_analytics",
        company="PetSmart",
        role="Manager, Business Analytics",
        preferred_bullets=3,
        max_bullets=4,
        max_words=120,
        source_prefix="Petsmart/PetSmart - Manager, Business Analytics.md",
        anchor_texts=("Manager, Business Analytics",),
    ),
    RoleSpec(
        role_id="petsmart_customer_insights_analyst",
        company="PetSmart",
        role="Customer Insights Analyst, Marketing Analytics",
        preferred_bullets=2,
        max_bullets=3,
        max_words=60,
        source_prefix="Petsmart/PetSmart - Customer Insights Analyst.md",
        anchor_texts=("Customer Insights Analyst, Marketing Analytics",),
    ),
)

ROLE_SPECS_BY_ID = {spec.role_id: spec for spec in ROLE_SPECS}

# This is a ceiling, not a target. The prompt explicitly allows a shorter summary.
SUMMARY_MAX_WORDS = 45

# Backward-compatible alias in case another module still imports the old name.
# New prompt code should use SUMMARY_MAX_WORDS.
SUMMARY_TARGET_WORDS = SUMMARY_MAX_WORDS

SUMMARY_PLACEHOLDER = "[SUMMARY GOES HERE]"
BULLETS_PLACEHOLDER = "[BULLET POINTS GO HERE]"


class EvidenceBullet(BaseModel):
    """A generated bullet and the career-history files supporting it."""

    text: str
    source_files: list[str]


class TailoredRole(BaseModel):
    """Generated content for one fixed role block."""

    role_id: str
    company: str
    role: str
    bullets: list[EvidenceBullet]


class JobMetadata(BaseModel):
    """Job metadata used only for output naming."""

    company_name: str
    job_id: str


class TailoredResume(BaseModel):
    """Fully drafted and evidence-attributed resume content."""

    summary: str
    summary_source_files: list[str]
    roles: list[TailoredRole]
    metadata: JobMetadata

    def role_by_id(self, role_id: str) -> TailoredRole:
        for role in self.roles:
            if role.role_id == role_id:
                return role
        raise KeyError(role_id)


def count_words(value: str) -> int:
    """Count whitespace-delimited words for soft resume length checks."""

    return len(value.split())
