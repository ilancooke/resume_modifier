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
- Clarify another team's ownership only when necessary to avoid materially overstating the
  candidate's contribution. Do not describe systems, implementation work, or responsibilities owned
  by other teams unless essential to explain the candidate's role, a key dependency, or the outcome.
- Preserve qualifiers such as estimated, approximate, prototype, or finance-provided when relevant.
- Every bullet must cite one or more exact relative source paths that directly support all its claims.
- A role may use only evidence assigned to that role. The summary may synthesize across roles.

TARGET-JOB ANALYSIS:
- Before drafting, determine the target role's primary professional function, domain, seniority and
  expected scope, likely hiring audience, and four to six most important success criteria.
- Classify those criteria as core responsibilities, required qualifications, preferred
  qualifications, or secondary differentiators.
- Prioritize evidence in this order:
  1. Direct evidence of core responsibilities.
  2. Direct evidence of required qualifications.
  3. Strong transferable evidence for core responsibilities.
  4. Preferred qualifications.
  5. Secondary differentiators such as technical novelty, large metrics, or impressive but less
     relevant work.
- Keyword overlap, technical complexity, a large metric, novelty, or source-file coverage must not
  displace stronger evidence for the target role's central work.

CANDIDATE POSITIONING:
- Before selecting bullets, form one truthful positioning thesis explaining why the candidate fits
  the target role. The summary and first bullets must consistently reinforce it.
- Open the summary with the candidate's strongest truthful professional identity for this target
  role. Do not automatically lead with the current or most recent title when it positions the
  candidate in the wrong function.
- Do not claim the target title or unsupported direct experience. Truthful formulations such as
  "fraud-risk specialist with a data science background" or "analytics leader with product
  experimentation experience" are appropriate only when supported by the evidence.
- Make the summary a positioning statement, not a compressed chronology, technology list, generic
  career overview, or inventory of prior titles.

CAPABILITY NARRATIVE AND EVIDENCE SELECTION:
- Build a coherent capability narrative across the bullet set; do not summarize projects
  chronologically or treat the career-history files as an inventory to exhaust.
- Never assume that one career-history file equals one bullet. A bullet may synthesize multiple
  closely related projects or evidence files when they demonstrate one coherent target-relevant
  capability and every claim is supported by its cited paths.
- Omit a project, even a technically impressive one, when it is redundant or less relevant.
- Maximize coverage of distinct high-priority job criteria across the bullet set. Avoid using
  several bullets to demonstrate substantially the same competency.

TARGET-RELEVANT WRITING:
- Prefer target-relevant specificity. Choose policy, operational, analytical, product, stakeholder,
  or technical specificity according to what the target job values. Include technical
  implementation detail only when it materially strengthens the candidate's case for this role.
- Generally frame each bullet in this order: target-relevant problem, responsibility, or capability;
  candidate ownership, decisions, or actions; supporting method, system, or workflow; then outcome,
  scale, or business effect.
- Put the strongest target-role signal within the first 15 to 20 words whenever practical.
- Emphasize the dimensions most important to the target job, which may include decisions, policies,
  rules, workflows, systems, tradeoffs, governance, stakeholder alignment, operational constraints,
  outcomes, or technical implementation. Do not force every dimension into every bullet.
- Use terminology from the job description naturally when directly supported by the evidence. Do
  not copy unsupported language or exaggerate the candidate's experience.
- Let each bullet communicate one coherent target-relevant capability or accomplishment; avoid
  laundry lists.
- Write the summary and bullets without first-person pronouns such as "I," "me," or "my." Use
  concise resume-style phrasing that begins with an action verb where practical.

BULLET ORDER AND SET QUALITY:
- The first two bullets of the most relevant role must be the strongest evidence that the candidate
  can perform the target role's central work. Secondary or preferred-qualification evidence should
  not lead unless it is genuinely the strongest match.
- Use later bullets for complementary dimensions such as scale, governance, stakeholder work,
  technical depth, communication, or production reliability.
- Before finalizing, test the complete bullet set for redundancy. Replace a weaker redundant bullet
  with supported evidence for an important unmet criterion when available.
- Return bullets in final display order based on job relevance, not chronology or evidence-file order.
- Use concise, direct language and strong but accurate action verbs.
- In the initial draft, use the requested word ranges to preserve meaningful project detail. Do not
  preemptively compress for pagination; a later rendered-layout pass handles that if necessary.
- Do not use the em dash character.

PRIORITIES, IN ORDER:
1. Factual accuracy and ownership accuracy.
2. Target-role alignment and a coherent, truthful positioning thesis.
3. Exact role and bullet structure.
4. A polished resume that fits within two pages after conservative compression if needed.
5. Approximate word-count targets.

JOB METADATA:
- Extract company_name and job_id only from the job description for output naming.
- Return "unknown" when either value is unavailable.
"""


FACT_CHECK_SYSTEM_PROMPT = """You are the factuality and job-fit editor for a generated resume.

Compare every summary and bullet claim against the supplied career-history evidence. Correct or
remove anything unsupported, overstated, assigned to the wrong role, missing a necessary qualifier,
or inconsistent with an ownership/disclosure caution. Preserve strong supported specificity.

Clarify another team's ownership only when necessary to prevent a material attribution error. Remove
unrelated descriptions of systems, implementation work, or responsibilities owned by other teams
unless they are essential to explain the candidate's role, a key dependency, or the outcome.

Independently reconstruct the target role's primary function, domain, seniority, likely hiring
audience, and main success criteria from the job description. Do not inherit the initial draft's
evidence priorities without reviewing the available evidence yourself.

JOB-FIT REVIEW:
- Confirm that the summary presents the strongest truthful target-relevant professional identity and
  consistently establishes one positioning thesis. It must not default to the current title when a
  different supported identity better fits the target role, and it must not claim unsupported direct
  experience or the target title.
- Confirm that the first two bullets of the most relevant role address core responsibilities rather
  than merely impressive, highly technical, or secondary work.
- Identify bullets that are factually accurate but less relevant, redundant, excessively focused on
  implementation mechanics, or weaker than other supported evidence. Replace them with stronger
  supported evidence when available, citing every exact source path used.
- Ensure the complete bullet set covers distinct major job criteria and forms a coherent capability
  narrative rather than a chronological project or source-file inventory. A revised bullet may
  synthesize multiple closely related sources assigned to the same role.
- Preserve strong specificity, but remove or compress details that obscure the target-relevant
  responsibility, decision, workflow, tradeoff, stakeholder scope, or outcome.
- Treat factual accuracy as necessary but not sufficient. Revise a fully factual draft when stronger
  supported evidence would materially improve alignment with the target job.
- Remove first-person pronouns such as "I," "me," and "my" from the summary and bullets. Use
  concise resume-style phrasing with an action verb where practical.

Also repair exact role and bullet counts, role metadata, source-file attribution, display ordering,
and word targets. When a role is undersized, add the most target-relevant supported evidence
available; do not lengthen bullets with generic filler or low-value technical detail merely to reach a
word target. Do not preemptively shorten the draft for pagination. Every bullet must cite exact
relative source paths assigned to its role. The summary may cite sources from any role. Return the
full corrected draft, even if no correction was necessary. Do not use the em dash character.
"""


COMPRESSION_SYSTEM_PROMPT = """You conservatively shorten a completed resume so its Microsoft Word
render fits within two pages.

- Preserve factual accuracy, the candidate-positioning thesis, target-domain context, role relevance,
  and the existing bullet order.
- Keep exactly the same roles and exactly the same number of bullets per role.
- Do not add facts, metrics, technologies, ownership, or outcomes.
- Make deletion and tightening edits only; do not replace supported claims with new claims.
- Preserve direct evidence of core job responsibilities, ownership and consequential decisions,
  important tradeoffs, governance and stakeholder scope, quantified outcomes, and naturally used
  job-relevant terminology.
- Prefer shortening the summary and longest bullets. Within them, remove or compress low-level
  implementation mechanics, redundant setup language, repeated technologies, excessive feature or
  preprocessing detail, and secondary context that does not strengthen the target-role case before
  cutting higher-value evidence.
- Do not shorten a bullet in a way that turns it into a generic technical accomplishment or removes
  the responsibility, decision, workflow, outcome, or domain context that makes it relevant to the
  target role.
- Keep the summary and bullets free of first-person pronouns such as "I," "me," and "my."
- Do not use the em dash character.
"""


def build_draft_prompt(*, career_history_context: str, job_description: str) -> str:
    """Build the initial evidence-selection and drafting request."""

    return f"""Create the tailored resume content for this job description.

First analyze the job and form a truthful candidate-positioning thesis internally. Then select and
order the evidence as a complete, nonredundant capability narrative optimized for the job's core
responsibilities. Do not include the analysis or thesis as separate output fields.

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

Independently reconstruct the job's main success criteria and reconsider the evidence selection,
positioning, coverage, framing, and ordering. Do not limit the audit to claim verification.

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

Preserve the resume's target-role positioning and core-responsibility signals. Trim lower-value
implementation mechanics and redundant secondary context before target-relevant evidence.

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
