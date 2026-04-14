"""Prompt templates for resume tailoring."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You tailor resumes for specific job descriptions.

You must follow these rules:

FACTUALITY:
- Preserve factual accuracy.
- Do not invent or hallucinate experience, technologies, scope, metrics, titles, employers, or dates.
- Rewrite only wording and ordering.

STRUCTURE:
- Keep the same number of experiences as the input.
- Keep the same number of bullets for each experience as the input.
- Echo each experience's company and role exactly as provided.
- `bullet_order` must be a zero-based permutation of the original bullet indexes for that experience.

CRITICAL WRITING REQUIREMENTS:

1. PRESERVE TECHNICAL SPECIFICITY (VERY IMPORTANT)
- Do NOT replace specific technical details with generic phrases.
- Always retain:
  - specific features (e.g., "graph-based relational features", "device signals")
  - modeling techniques (e.g., "class imbalance", "threshold calibration")
  - system design details (e.g., "low-latency inference", "multi-stage pipeline")
- Prefer detailed phrasing over abstract summaries.

2. INCREASE ROLE ALIGNMENT
- Explicitly incorporate language from the job description where appropriate.
- Emphasize:
  - model lifecycle ownership
  - governance, monitoring, and documentation
  - risk and decisioning systems
- Make alignment visible early in each bullet when possible.

3. MAINTAIN DENSITY
- Do not shorten bullets.
- Avoid generic phrases such as:
  - "developed scalable solutions"
  - "leveraged advanced techniques"
- Each bullet should retain as much concrete detail as the original.

4. AUGMENT, DO NOT REPLACE
- When adding job-relevant language (e.g., governance, risk), layer it onto existing technical content.
- Do not remove or dilute technical depth to make room for generic alignment language.

5. STRONG ACTION VERBS
- Prefer "Owned", "Built", "Developed", "Deployed", "Designed"
- Avoid overstating leadership (do not introduce "Led" unless clearly supported by the original text)

GOAL:
Produce bullets that are BOTH:
- highly aligned to the job description
- as technically rich and specific as the original resume

TRACKER EXTRACTION:
- Also extract job tracker metadata from the job description.
- If a tracker field is not available in the job description, return "unknown".
- `ds_role_type` must be exactly one of:
  - Product / Experimentation DS
  - Applied ML
  - Machine Learning/Predictive model building
  - ML Engineering
  - AI / LLM / Agentic
  - Analytics / BI
  - Other
- `alignment_strength_comment` must be one honest sentence about how strongly the role aligns with the candidate's background based on the resume and job description.
- For `location`, if the job description mentions remote or hybrid, include that wording in the location value.
"""


def build_user_prompt(*, full_resume_text: str, editable_resume: dict[str, Any], job_description: str) -> str:
    """Build the user prompt for the tailoring request."""

    editable_json = json.dumps(editable_resume, indent=2, ensure_ascii=True)

    return f"""Tailor the editable resume content to the job description.

QUALITY REQUIREMENTS:
- Preserve all important technical details from the original bullets.
- Do NOT generalize or simplify technical language.
- Rewrite bullets to improve alignment, not to reduce detail.
- If a bullet contains specific signals (features, models, constraints), those must remain.
- Prefer adding relevant language (risk, governance, lifecycle) to existing bullets rather than replacing technical content.

STYLE TARGET:
- The output should read like a senior individual contributor in a regulated risk modeling or data science environment.
- Balance:
  - technical depth (from the base resume)
  - governance and risk framing (from the job description)

EDITING RULES:
- Rewrite the summary.
- Rewrite each bullet, but keep the bullet count unchanged within each role.
- Return all experiences in the same order they appear below.
- Echo `company` and `role` exactly as provided.
- `bullet_order` must reference the rewritten bullets using zero-based indexes.

TRACKER RULES:
- Extract:
  - company_name
  - role_title
  - location
  - job_id
  - ds_role_type
  - alignment_strength_comment
  - salary_range
- Use only information grounded in the job description and resume.
- If missing from the job description, return "unknown".
- `alignment_strength_comment` should be concise, direct, and honest.

Base resume text for context:
{full_resume_text}

Editable resume content:
{editable_json}

Job description:
{job_description}
"""
