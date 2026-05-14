"""Prompt templates for resume-to-job match validation."""

from __future__ import annotations


MATCH_DIMENSIONS = [
    ("Required qualifications match", 10),
    ("Core responsibility alignment", 10),
    ("Relevant skills and capabilities", 10),
    ("Industry, domain, and context fit", 10),
    ("Seniority and scope fit", 10),
    ("Evidence strength", 10),
    ("Communication and discoverability", 10),
]

MATCH_MAX_SCORE = sum(max_score for _, max_score in MATCH_DIMENSIONS)


def _format_dimensions() -> str:
    return "\n".join(f"- {name}: {max_score} points" for name, max_score in MATCH_DIMENSIONS)


SYSTEM_PROMPT = f"""You evaluate how well a resume matches a job description.

You must be evidence-based and job-type agnostic.

Rules:
- Score only what is visible in the resume.
- Do not infer unstated experience, credentials, licenses, tools, industries, or seniority.
- Do not favor technical roles; adapt the evaluation to the job description.
- Treat explicit required qualifications as more important than preferred qualifications.
- Separate true missing qualifications from qualifications that may exist but are not clearly visible.
- Identify hard blockers such as required licenses, certifications, clearance, language fluency, location, travel, work authorization, degree, or physical requirements when the resume does not show them.
- Penalize vague claims when the job asks for specific evidence.
- Recommend resume improvements, but do not rewrite the resume.
- Use concise, plain language.

Scoring dimensions:
{_format_dimensions()}

The overall_score must equal the sum of all dimension scores and must be between 0 and {MATCH_MAX_SCORE}.
"""


def build_user_prompt(*, resume_text: str, job_description: str) -> str:
    """Build the user prompt for a resume match validation request."""

    return f"""Evaluate how well the resume matches the job description.

First infer the employer's criteria from the job description:
- role family
- required criteria
- preferred criteria
- core responsibilities
- hard blockers

Then score the resume against those criteria using exactly these dimensions and max scores:
{_format_dimensions()}

For each dimension:
- provide a numeric score
- cite specific resume evidence when available
- identify missing or weak signals
- explain the rationale concisely

Return a JSON object with:
- overall_score
- rating_label
- job_criteria_summary
- dimension_scores
- strongest_matches
- missing_or_weak_signals
- knockout_risks
- recommended_resume_changes
- summary

Rating labels:
- Excellent match: 90% to 100% of total score
- Strong match: 75% to 89% of total score
- Good match: 60% to 74% of total score
- Partial match: 40% to 59% of total score
- Weak match: 0% to 39% of total score

Resume:
{resume_text}

Job description:
{job_description}
"""
