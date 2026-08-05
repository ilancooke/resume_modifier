"""Prompt templates for evidence-based resume drafting and compression."""

from __future__ import annotations

import json

from resume_models import ROLE_SPECS, SUMMARY_TARGET_WORDS, TailoredResume


def _role_requirements() -> str:
    return "\n".join(
        f"- {spec.role_id}: exactly {spec.bullet_count} bullets; aim for "
        f"{round(spec.target_words * 0.85)}-{round(spec.target_words * 1.15)} words total "
        f"(target approximately {spec.target_words})"
        for spec in ROLE_SPECS
    )


DRAFT_SYSTEM_PROMPT = """You draft a job-targeted resume from detailed career-history evidence.

The career-history files are the only factual authority. Follow their ownership boundaries,
uncertainty notes, disclosure cautions, and claims requiring caution as hard constraints.

FACTUALITY:
- Do not invent or strengthen metrics, scope, ownership, leadership, technologies, dates, titles,
  causality, financial impact, model performance, or outcomes.
- Distinguish personal work from team, engineering, product, finance, and operations work.
- Preserve qualifiers such as estimated, approximate, prototype, or finance-provided when relevant.
- Every bullet must cite one or more exact relative source paths that directly support all its claims.
- A role may use only evidence assigned to that role. The summary may synthesize across roles.

SELECTION AND WRITING:
- Infer the target job's most important success criteria.
- Select the strongest supported projects and details for those criteria; omit weaker projects.
- Prefer quantified accomplishments, consequential decisions, technical specificity, and clear outcomes.
- Let each bullet cover a coherent accomplishment; avoid repetitive bullets or laundry lists.
- Return bullets in final display order, strongest and most relevant first.
- Use concise, direct language and strong but accurate action verbs.
- In the initial draft, use the requested word ranges to preserve meaningful project detail. Do not
  preemptively compress for pagination; a later rendered-layout pass handles that if necessary.
- Do not use the em dash character.

PRIORITIES, IN ORDER:
1. Factual accuracy and ownership accuracy.
2. Exact role and bullet structure.
3. A polished resume that fits within two pages after conservative compression if needed.
4. Approximate word-count targets.

JOB METADATA:
- Extract company_name and job_id only from the job description for output naming.
- Return "unknown" when either value is unavailable.
"""


FACT_CHECK_SYSTEM_PROMPT = """You are the factuality editor for a generated resume.

Compare every summary and bullet claim against the supplied career-history evidence. Correct or
remove anything unsupported, overstated, assigned to the wrong role, missing a necessary qualifier,
or inconsistent with an ownership/disclosure caution. Preserve strong supported specificity.

Also repair exact role and bullet counts, role metadata, source-file attribution, ordering, and word
targets. Expand an undersized role with additional supported detail rather than generic filler. Do not
preemptively shorten the draft for pagination. Every bullet must cite exact relative source paths
assigned to its role. The summary may cite sources from any role. Return the full corrected draft,
even if no correction was necessary. Do not use the em dash character.
"""


COMPRESSION_SYSTEM_PROMPT = """You conservatively shorten a completed resume so its Microsoft Word
render fits within two pages.

- Preserve factual accuracy, role relevance, and the existing bullet order.
- Keep exactly the same roles and exactly the same number of bullets per role.
- Do not add facts, metrics, technologies, ownership, or outcomes.
- Make deletion and tightening edits only; do not replace supported claims with new claims.
- Preserve the most important quantified evidence and job-relevant details.
- Prefer shortening the summary and longest bullets and removing redundant setup language.
- Do not use the em dash character.
"""


def build_draft_prompt(*, career_history_context: str, job_description: str) -> str:
    """Build the initial evidence-selection and drafting request."""

    return f"""Create the tailored resume content for this job description.

Hard structure and drafting targets:
- Summary: aim for 43-58 words (target approximately {SUMMARY_TARGET_WORDS})
{_role_requirements()}
- Return roles in the exact order shown above.
- Echo each role's company and role name exactly as defined in the evidence headers.

For each bullet, return:
- `text`: final resume bullet text without a bullet glyph
- `source_files`: exact relative path(s) from the evidence supporting every claim

Return `summary_source_files` using exact relative paths as well.

Career-history evidence:
{career_history_context}

Job description:
{job_description}
"""


def build_fact_check_prompt(
    *, career_history_context: str, job_description: str, draft: TailoredResume
) -> str:
    """Build the independent grounding and repair request."""

    draft_json = json.dumps(draft.model_dump(), indent=2, ensure_ascii=True)
    return f"""Audit and, where necessary, correct this generated resume draft.

Required structure and drafting targets:
- Summary: aim for 43-58 words (target approximately {SUMMARY_TARGET_WORDS})
{_role_requirements()}

Generated draft:
{draft_json}

Career-history evidence:
{career_history_context}

Job description:
{job_description}
"""


def build_compression_prompt(
    *, tailoring: TailoredResume, overflow_text: str, overflow_word_count: int, attempt: int
) -> str:
    """Build a request to recover enough space to meet the hard two-page limit."""

    tailored_json = json.dumps(tailoring.model_dump(), indent=2, ensure_ascii=True)
    return f"""Shorten this resume enough to fit within two rendered pages.

Compression attempt: {attempt}
Extracted text beyond page 2: {overflow_word_count} words

Overflow text:
{overflow_text or "[no extractable text]"}

Keep the exact role order and bullet counts:
{_role_requirements()}

Return only:
- `summary`
- `roles`, each containing `role_id` and its ordered `bullets` as strings

Resume to shorten:
{tailored_json}
"""
