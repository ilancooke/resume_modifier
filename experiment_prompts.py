"""Blinded pairwise prompts and schemas for resume experiments."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DIMENSIONS = (
    "employer_alignment",
    "evidence_strength",
    "ownership_clarity",
    "outcomes_and_credibility",
    "requirements_coverage",
    "concision_and_scanability",
    "interview_likelihood",
)

ResumeScore = Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
ConfidenceScore = IntEnum(
    "ConfidenceScore", {f"score_{value}": value for value in range(101)}
)

PAIRWISE_SYSTEM_PROMPT = """You are a rigorous, impartial resume evaluator.

Compare two resumes for the same candidate against one job description. Evaluate only what is
visible in the supplied resumes and job description. Do not infer which workflow produced either
resume. Do not reward length, keyword repetition, or confident wording by itself. Prefer specific,
credible evidence that makes the candidate's contribution clear.

Score both resumes independently on every requested dimension, then choose the stronger overall
resume. A tie is valid when neither has a meaningful advantage. Treat unsupported-looking claims as
credibility concerns, but do not claim to have fact-checked them because source evidence is not
provided. Base the overall winner on likely interview value, not a mechanical sum alone.
"""

ABSOLUTE_SYSTEM_PROMPT = """You are a rigorous resume evaluator.

Evaluate one resume against one job description using only the supplied text. Do not compare it to
an imagined alternative resume. Do not reward length, keyword repetition, or confident wording by
itself. Prefer specific, credible evidence that makes the candidate's contribution clear. Treat
unsupported-looking claims as credibility concerns, but do not claim to have fact-checked them
because source evidence is not provided.
"""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DimensionScore(StrictModel):
    dimension: str
    resume_x: ResumeScore
    resume_y: ResumeScore
    rationale: str


class PairwiseComparison(StrictModel):
    dimension_scores: list[DimensionScore]
    winner: Literal["X", "Y", "tie"]
    confidence: int = Field(ge=0, le=100)
    overall_rationale: str
    resume_x_strongest_advantage: str
    resume_y_strongest_advantage: str
    credibility_concerns: list[str]

    @model_validator(mode="after")
    def validate_dimensions(self) -> "PairwiseComparison":
        names = [score.dimension for score in self.dimension_scores]
        if names != list(DIMENSIONS):
            raise ValueError(f"Dimensions must appear exactly in this order: {list(DIMENSIONS)}")
        return self


class WireDimensionScore(StrictModel):
    """One dimension in the provider-facing object schema."""

    resume_x: ResumeScore
    resume_y: ResumeScore
    rationale: str


class WireDimensionScores(StrictModel):
    """Required named dimensions; object keys eliminate list-order failures."""

    employer_alignment: WireDimensionScore
    evidence_strength: WireDimensionScore
    ownership_clarity: WireDimensionScore
    outcomes_and_credibility: WireDimensionScore
    requirements_coverage: WireDimensionScore
    concision_and_scanability: WireDimensionScore
    interview_likelihood: WireDimensionScore


class PairwiseComparisonWire(StrictModel):
    """Provider-facing schema converted to the stable stored representation."""

    dimension_scores: WireDimensionScores
    winner: Literal["X", "Y", "tie"]
    confidence: ConfidenceScore
    overall_rationale: str
    resume_x_strongest_advantage: str
    resume_y_strongest_advantage: str
    credibility_concerns: list[str]

    def to_comparison(self) -> PairwiseComparison:
        return PairwiseComparison(
            dimension_scores=[
                DimensionScore(
                    dimension=dimension,
                    **getattr(self.dimension_scores, dimension).model_dump(),
                )
                for dimension in DIMENSIONS
            ],
            winner=self.winner,
            confidence=int(self.confidence),
            overall_rationale=self.overall_rationale,
            resume_x_strongest_advantage=self.resume_x_strongest_advantage,
            resume_y_strongest_advantage=self.resume_y_strongest_advantage,
            credibility_concerns=self.credibility_concerns,
        )


class AbsoluteDimensionScore(StrictModel):
    score: ResumeScore
    rationale: str


class AbsoluteDimensionScores(StrictModel):
    employer_alignment: AbsoluteDimensionScore
    evidence_strength: AbsoluteDimensionScore
    ownership_clarity: AbsoluteDimensionScore
    outcomes_and_credibility: AbsoluteDimensionScore
    requirements_coverage: AbsoluteDimensionScore
    concision_and_scanability: AbsoluteDimensionScore
    interview_likelihood: AbsoluteDimensionScore


class AbsoluteEvaluation(StrictModel):
    """One provenance-blind resume assessment against a job description."""

    dimension_scores: AbsoluteDimensionScores
    confidence: ConfidenceScore
    overall_rationale: str
    strongest_advantage: str
    most_important_weakness: str
    credibility_concerns: list[str]

    def flat_scores(self) -> dict[str, int]:
        return {
            dimension: int(getattr(self.dimension_scores, dimension).score)
            for dimension in DIMENSIONS
        }


def build_pairwise_prompt(
    *, job_description: str, resume_x: str, resume_y: str
) -> str:
    """Build one order-specific, provenance-blind comparison prompt."""

    dimension_lines = "\n".join(f"- {dimension}" for dimension in DIMENSIONS)
    return f"""Compare Resume X and Resume Y for this job.

Score each resume from 1 to 10 on every named dimension below. Return exactly one score object under
each corresponding dimension key:
{dimension_lines}

Return an overall winner of X, Y, or tie and a confidence from 0 to 100. Keep rationales concise and
comparative. List only genuine credibility concerns; return an empty list when there are none.

JOB DESCRIPTION
===============================
{job_description}

RESUME X
===============================
{resume_x}

RESUME Y
===============================
{resume_y}
"""


def build_absolute_prompt(*, job_description: str, resume: str) -> str:
    """Build a provenance-blind absolute scoring prompt for one resume."""

    dimension_lines = "\n".join(f"- {dimension}" for dimension in DIMENSIONS)
    return f"""Evaluate this resume for this job.

Score the resume from 1 to 10 on every named dimension below. Return exactly one score object under
each corresponding dimension key:
{dimension_lines}

Use the full 1-to-10 range when warranted. Keep rationales concise. Set confidence from 0 to 100.
List only genuine credibility concerns; return an empty list when there are none.

JOB DESCRIPTION
===============================
{job_description}

RESUME
===============================
{resume}
"""
