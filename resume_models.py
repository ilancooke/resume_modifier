"""Shared models and role requirements for generated resumes."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Fixed output requirements for one resume role block."""

    role_id: str
    company: str
    role: str
    bullet_count: int
    target_words: int
    source_prefix: str
    anchor_texts: tuple[str, ...]


ROLE_SPECS = (
    RoleSpec(
        role_id="amazon_data_scientist",
        company="Amazon",
        role="Data Scientist, Marketplace Fraud & Abuse",
        bullet_count=6,
        target_words=400,
        source_prefix="Amazon/Data Scientist/",
        anchor_texts=("Data Scientist, Marketplace Fraud & Abuse",),
    ),
    RoleSpec(
        role_id="amazon_ba_bie",
        company="Amazon",
        role="Business Analyst / Business Intelligence Engineer",
        bullet_count=3,
        target_words=100,
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
        bullet_count=2,
        target_words=80,
        source_prefix="Petsmart/PetSmart - Manager, Business Analytics.md",
        anchor_texts=("Manager, Business Analytics",),
    ),
    RoleSpec(
        role_id="petsmart_customer_insights_analyst",
        company="PetSmart",
        role="Customer Insights Analyst, Marketing Analytics",
        bullet_count=1,
        target_words=50,
        source_prefix="Petsmart/PetSmart - Customer Insights Analyst.md",
        anchor_texts=("Customer Insights Analyst, Marketing Analytics",),
    ),
)

ROLE_SPECS_BY_ID = {spec.role_id: spec for spec in ROLE_SPECS}
SUMMARY_TARGET_WORDS = 50
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
    """Count whitespace-delimited words for soft resume length targets."""

    return len(value.split())
