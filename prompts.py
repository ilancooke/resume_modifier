"""Prompt templates for resume tailoring."""

from __future__ import annotations

import json
from typing import Any

REWRITE_SYSTEM_PROMPT = """You rewrite resume content for specific job descriptions.

You must follow these rules:

FACTUALITY:
- Preserve factual accuracy.
- Do not invent or hallucinate experience, technologies, scope, metrics, titles, employers, dates, ownership, or outcomes.
- All rewritten content must remain grounded in the original resume.

STRUCTURE:
- Keep the same number of experiences as the input.
- Keep the same number of bullets for each experience as the input.
- Do NOT drop, combine, split, or add bullets.
- Echo each experience's company and role exactly as provided.
- Return each experience's `bullets` in the exact same order as the input bullets.
- Each rewritten bullet at index N must correspond to the original bullet at index N.
- Do not reorder bullets in this step.
- The summary may be substantially rewritten, but it must remain factually grounded in the resume.

WRITING QUALITY:
- Preserve important technical specificity, including:
  - features, signals, and data sources
  - modeling techniques and constraints
  - system design details
  - scale, metrics, and business outcomes
- Do NOT replace concrete details with generic filler.
- Do NOT make superficial wording changes just to appear rewritten.
- Some bullets may remain mostly unchanged if they are already strong and relevant.
- In the most relevant experience, aim to materially reframe 1 to 3 bullets when doing so improves relevance, clarity, or signal priority.
- Do not reframe a bullet merely to satisfy a quota; preserve strong bullets when they are already well-aligned.
- Materially reframed means the emphasis and value proposition clearly change, not just a few words.
- Reduce redundancy across bullets when possible.
- Different bullets should emphasize distinct aspects of the work rather than repeating the same project, analysis, model, or system framing.
- You may tighten wording for clarity, but do not remove important technical or business detail.

STYLE:
- Use strong but accurate action verbs such as: Owned, Built, Designed, Developed, Deployed, Implemented.
- Do not introduce stronger leadership claims than supported by the original resume.
- Do not use the em dash character (—).

OUTPUT GOAL:
Produce a resume that is more relevant to the target role because the most important matching signals are clearer and more prominent, not because job-description keywords were mechanically inserted.

JOB METADATA EXTRACTION:
- Extract:
  - company_name
  - job_id
- These fields are used only for naming the generated output files.
- Use only information grounded in the job description.
- If a field is not available, return "unknown".
"""


def build_rewrite_prompt(*, full_resume_text: str, editable_resume: dict[str, Any], job_description: str) -> str:
    """Build the user prompt for the rewrite-only request."""

    editable_json = json.dumps(editable_resume, indent=2, ensure_ascii=True)

    return f"""Rewrite the editable resume content to the job description.

This is a relevance and clarity task, not a keyword-matching task.

Follow this process:

1. Identify the 3 to 5 most important signals in the job description.
2. Find the strongest evidence for those signals in the resume.
3. Rewrite bullets so those signals are clearer while keeping each bullet mapped to its original index.

Task requirements:
- Keep the same number of bullets per role.
- Do not drop, combine, split, or add bullets.
- Keep bullets in the exact same order as the input.
- Each rewritten bullet must remain grounded in the original bullet at the same index.
- In the most relevant experience, aim to materially reframe 1 to 3 bullets when doing so improves relevance, clarity, or signal priority.
- Do not reframe a bullet merely to satisfy a quota; preserve strong bullets when they are already well-aligned.
- Prefer stronger framing around decisions enabled, business impact, tradeoffs, experimentation, analytics, or system outcomes when supported by the resume.
- Avoid repeating the same project, analysis, model, or system framing across multiple bullets.

Signal prioritization:
- Infer the role's primary success criteria from the job description instead of assuming a fixed role category.
- Prioritize required qualifications, core responsibilities, and repeated themes over preferred or incidental keywords.
- Favor resume evidence that directly supports the target role's main outcomes, scope, tools, domain, stakeholders, or operating environment.
- If the job description is vague, prioritize broadly transferable evidence such as measurable outcomes, technical depth, ownership, collaboration, and communication.

Final self-check before answering:
- Are the most important role signals clearer in the summary and rewritten bullets?
- Were bullets materially reframed only when doing so improved relevance, clarity, or signal priority?
- Does the output feel meaningfully more tailored, rather than lightly reworded?
- Did every rewritten bullet remain mapped to the original bullet at the same index?

Return a JSON object with:
- `summary`
- `experiences`
- `metadata`

Each item in `experiences` must contain:
- `company`
- `role`
- `bullets`

Base resume text for context:
{full_resume_text}

Editable resume content:
{editable_json}

Job description:
{job_description}
"""


BULLET_ORDER_SYSTEM_PROMPT = """You order rewritten resume bullets for a specific job description.

You must follow these rules:

- Do not rewrite, edit, add, remove, combine, or split bullets.
- Return only the final bullet order for each experience.
- `bullet_order` must be a zero-based permutation of the rewritten bullet indexes for that experience.
- Because rewritten bullets are kept in original source order, each index also corresponds to the original source bullet at the same index.
- Echo each experience's company and role exactly as provided.
- Reorder bullets only when it improves signal priority.
- Keep the original order when it already presents the strongest evidence first.
- The first 2 to 3 bullets in the most relevant experience should prioritize the strongest supported matches to the role.
- The first bullet in the most relevant experience should emphasize the strongest supported match to one of the role's most important signals.
"""


def build_bullet_order_prompt(
    *,
    editable_resume: dict[str, Any],
    rewritten_resume: dict[str, Any],
    job_description: str,
) -> str:
    """Build the user prompt for the order-only request."""

    editable_json = json.dumps(editable_resume, indent=2, ensure_ascii=True)
    rewritten_json = json.dumps(rewritten_resume, indent=2, ensure_ascii=True)

    return f"""Choose the final display order for each experience's rewritten bullets.

This is an ordering-only task. Do not rewrite any bullet text.

Use this process:

1. Infer the role's primary success criteria from the job description.
2. Compare those criteria against the rewritten bullets.
3. Return the bullet indexes in the order they should appear in the tailored resume.

Ordering guidance:
- Prioritize required qualifications, core responsibilities, and repeated themes over preferred or incidental keywords.
- Favor bullets that directly support the target role's main outcomes, scope, tools, domain, stakeholders, or operating environment.
- When multiple signals are important, order bullets so the strongest supported matches appear first.
- If the job description is vague, prioritize broadly transferable evidence such as measurable outcomes, technical depth, ownership, collaboration, and communication.
- Preserve original order when reordering would not improve signal priority.

Indexing:
- Use zero-based indexes in `bullet_order`.
- If the final order should be original bullets 3, 1, 2, return `bullet_order`: [2, 0, 1].

Return a JSON object with:
- `experiences`

Each item in `experiences` must contain:
- `company`
- `role`
- `bullet_order`

Original editable resume content:
{editable_json}

Rewritten resume content, with bullets still in original source order:
{rewritten_json}

Job description:
{job_description}
"""
