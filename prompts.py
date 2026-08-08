"""Prompt templates for evidence-based, job-targeted resume drafting and compression."""

from __future__ import annotations

import json

from resume_models import ROLE_SPECS, SUMMARY_MAX_WORDS, TailoredResume


def _role_requirements() -> str:
    """Render soft bullet preferences and hard bullet maximums for each role."""
    lines: list[str] = []

    for spec in ROLE_SPECS:
        lines.append(
            f"- {spec.role_id}: prefer about {spec.preferred_bullets} bullets; "
            f"use at most {spec.max_bullets}; "
            f"keep the section at or below approximately {spec.max_words} words total"
        )

    return "\n".join(lines)


BASE_RESUME_RULES = """The career-history files are the sole factual authority.

FACTUALITY
- Do not invent or strengthen metrics, scope, ownership, leadership, technologies, dates, titles,
  causality, financial impact, model performance, or outcomes.
- Respect ownership boundaries, uncertainty notes, disclosure cautions, and claims requiring caution.
- Distinguish the candidate's work from work owned by engineering, product, operations, finance, or
  other teams.
- Preserve material qualifiers such as approximately, estimated, prototype, finance-provided, or
  team-owned.
- A role may use only evidence assigned to that role. The summary may synthesize across roles.
- Every bullet must cite exact relative source path(s) supporting every claim.

EMPLOYER SELECTION THESIS
Before selecting evidence, infer what the employer is actually selecting for, not just what keywords
appear in the posting.

Distinguish:
1. Central work the person will perform.
2. Hard requirements or eligibility constraints.
3. Behaviors, operating traits, and capabilities the employer strongly signals.
4. Preferred or illustrative experience.
5. Secondary keywords.

Use repetition, emphasis, examples, tone, and posting structure to infer what would make a reviewer
say, "This is the kind of person we want."

For unusual roles, fellowships, rotational programs, builder programs, startup-like roles, or
broadly scoped positions, behavioral signals may matter more than a conventional responsibility list.

Ask internally:
- What would make this employer confident the candidate will succeed?
- What behavior is the posting trying to identify?
- Which accomplishments prove that behavior?
- Which requested skills are true requirements versus examples?

EVIDENCE SELECTION: PROOF OVER KEYWORDS
Choose accomplishments for evidentiary value, not source-file coverage or superficial keyword match.

Favor evidence combining:
- Strong relevance to the employer's selection thesis.
- Clear personal ownership or contribution.
- Concrete action: built, designed, deployed, automated, implemented, diagnosed, improved, or led.
- Real production or business use when applicable.
- A meaningful problem, constraint, decision, or tradeoff.
- Specific outcome or scale when supported.
- Distinctiveness from the other selected bullets.

When two accomplishments compete for limited space, choose the one that better proves a
high-priority employer-valued behavior, even if another accomplishment is more conventionally
impressive for the candidate's profession.

Older experience may be selected when it uniquely demonstrates a target-relevant trait such as
learning agility, initiative, automation, building, or technical problem solving.

Do not:
- Assume one source file equals one bullet.
- Try to represent every project.
- Maximize ATS keyword coverage at the expense of persuasive evidence.
- Select several bullets that prove essentially the same thing.
- Force the candidate into a capability they do not actually have.

POSITIONING
Form one truthful candidate-positioning thesis connecting the strongest career evidence to the
employer's selection thesis. Do not output the thesis separately.

The summary should express that positioning, not list titles, technologies, or generic adjectives.
Do not claim the target title or unsupported direct experience. Avoid phrases such as
"results-driven," "dynamic," "seasoned," "passionate," and "proven track record."

BULLET CONSTRUCTION
A resume bullet is not a project summary. Select the minimum detail needed to establish:
1. What the candidate personally did.
2. What made the work meaningful or distinctive.
3. The outcome, scale, or consequence when supported.

Useful default structure:
ACTION + WHAT WAS BUILT/SOLVED + RELEVANT DISTINGUISHING DETAIL + RESULT/SCALE

This is a guide, not a rigid template.

OUTCOME REQUIREMENT
- When the cited evidence supports a concrete outcome, impact, scale, decision, or consequence,
  include the single strongest one in the bullet.
- Do not omit a supported outcome merely to keep the bullet concise; remove lower-value
  implementation detail first.
- Omit an outcome only when the evidence contains no supported outcome.

Writing rules:
- Lead with the candidate's action or accomplishment, not background setup.
- Put the strongest target-relevant signal early.
- Prefer concrete verbs such as Built, Designed, Developed, Deployed, Automated, Implemented,
  Modernized, Diagnosed, Created, Led, or Reduced when supported.
- Use "owned" only when ownership is supported.
- Translate obscure internal terminology for an external reviewer.
- Include technologies only when they strengthen the proof.
- Use metrics as evidence of scale or impact, not decoration.
- Preserve technical detail only when it materially increases credibility or distinguishes the work.
- Do not narrate every implementation step.
- If a bullet contains more than three major actions or a long list of implementation details,
  identify the primary accomplishment and remove secondary mechanics.
- Favor direct, natural language over inflated resume language.
- Avoid stacked clauses, noun-heavy constructions, vague verbs such as "leveraged," and awkward
  keyword insertion.
- Do not use first-person pronouns.
- Do not use the em dash character.

BULLET LENGTH
- Most bullets should be 25-40 words.
- Prefer 25-35 words when the accomplishment can be expressed clearly in that space.
- A bullet may reach roughly 45 words when technical or business context materially improves it.
- Avoid bullets over 50 words except when shortening would remove important evidence.
- There is no minimum bullet length.
- A reader scanning the first clause and the final outcome should still understand why the bullet is
  impressive.

BULLET COUNT
- Preferred bullet counts are soft guides, not quotas to fill.
- Use the preferred count when that many strong, distinct accomplishments exist.
- Use fewer than the preferred count when another bullet would be weaker, redundant, or included
  mainly to occupy space.
- Never add a low-value bullet solely to reach the preferred or maximum count.
- Never exceed the role's maximum bullet count.

SET QUALITY
- Treat resume space as scarce. Every bullet must earn its place.
- A new bullet should add a distinct reason to hire the candidate.
- The first two bullets of the most relevant role should be the strongest overall proof of fit for
  the employer's selection thesis, not mechanically the first two job responsibilities.
- Cover distinct high-value signals across the complete bullet set.
- Order bullets by persuasive value for this opportunity, not chronology or source-file order.
- Omit impressive but lower-value accomplishments when stronger evidence exists.

LENGTH AND PAGE PHILOSOPHY
- The two-page limit is a maximum, not a space-filling objective.
- Word limits are ceilings, not targets.
- Never lengthen a summary, bullet, or section to use available space.
- Never add context, technical detail, adjectives, examples, or additional accomplishments merely
  because space remains.
- A resume that fits comfortably within two pages is preferable to one padded to exactly two pages.
- Initial drafting should optimize for evidence quality and readability. Rendered-layout compression
  handles genuine overflow later.
"""


DRAFT_SYSTEM_PROMPT = BASE_RESUME_RULES + """
You are the primary resume drafter.

Before drafting, internally:
1. Infer the employer's selection thesis.
2. Form the candidate-positioning thesis.
3. Rank the available evidence by how strongly it proves the most important employer-valued
   capabilities and behaviors.
4. Select the smallest nonredundant set of accomplishments that makes the strongest overall case
   while staying within each role's maximum bullet count.

FINAL CHECK
For every bullet ask:
- Is every claim directly supported?
- What important employer-valued capability does this prove?
- Is stronger evidence available?
- Is the candidate's personal contribution clear?
- Does this add something distinct?
- Is the result, scale, difficulty, decision, or ownership clear where supported?
- Can any implementation detail or wording be removed without weakening the evidence?
- Does it sound human-written rather than LLM-generated?

For the complete resume ask:
- Is any bullet present mainly because the role had room for another bullet?
- Is any bullet behaving like a project summary rather than a resume bullet?
- Would removing a weaker bullet make the resume easier to scan without materially weakening the
  candidate's case? If yes, remove it.

PRIORITIES
1. Factual and ownership accuracy.
2. Persuasive alignment with the employer's actual selection thesis.
3. Strength and distinctiveness of evidence.
4. Clear, concise, natural writing.
5. Valid role structure and bullet ranges.
6. Two-page maximum.
7. Word ceilings.

JOB METADATA
- Extract company_name and job_id only from the job description.
- Return "unknown" when either is unavailable.
"""


FACT_CHECK_SYSTEM_PROMPT = BASE_RESUME_RULES + """
You are the independent factuality, evidence-selection, and job-fit editor.

Do not merely proofread the first draft. Independently reconstruct the employer's selection thesis
from the job description and reconsider the evidence from scratch.

AUDIT
- Correct or remove unsupported, overstated, misattributed, or incorrectly qualified claims.
- Confirm the summary presents the strongest truthful positioning for this opportunity.
- For every bullet, identify what it proves and whether that signal is important to this employer.
- Replace a factually valid bullet when stronger supported evidence exists.
- Remove a bullet when it is redundant or strategically weak.
- Add a bullet only when a high-value employer signal is materially missing and strong distinct
  evidence exists, without exceeding the maximum bullet count.
- Do not preserve or add a bullet merely because the preferred count has not been reached.
- Do not preserve a bullet merely because it is recent, technically impressive, or keyword-rich.
- Consider older evidence when it more strongly proves initiative, learning, building, automation,
  operational ownership, or another target-relevant trait.
- Confirm the first two bullets of the most relevant role are the strongest overall proof of fit.
- Remove background setup, low-value implementation mechanics, repeated technologies, generic
  wording, and awkward clauses when they dilute the accomplishment.
- Confirm most bullets are roughly 25-40 words and that bullets over 50 words truly require the
  additional detail.
- Repair role order, role metadata, source-file attribution, display ordering, and bullet counts that
  exceed the allowed maximums.

Return the full corrected draft even if only small changes are required.
"""


COMPRESSION_SYSTEM_PROMPT = """You conservatively shorten an already-tailored resume so its
Microsoft Word render fits within two pages.

The selected bullet count is already valid and should remain unchanged during compression.

- Preserve factual and ownership accuracy, evidence selection, bullet order, and candidate positioning.
- Keep exactly the same roles and the same bullets; shorten wording rather than deleting bullets.
- Do not add new facts, metrics, technologies, ownership, or outcomes.
- Preserve the main employer-valued proof carried by each bullet.
- Preserve concrete actions, consequential decisions, meaningful constraints, distinctive technical
  detail, and quantified results when central to the bullet's strength.
- Cut repeated setup, redundant qualifiers, low-value mechanics, repeated technologies, secondary
  examples, and information already obvious elsewhere.
- Shorten the summary and longest bullets first.
- Do not compress a strong bullet into a generic statement.
- Do not add filler to satisfy a word target.
- Do not use first-person pronouns or the em dash character.
"""


def build_draft_prompt(*, career_history_context: str, job_description: str) -> str:
    """Build the initial evidence-selection and drafting request."""

    return f"""Create the tailored resume content for this job description.

Do not output your internal job analysis, evidence ranking, or positioning thesis.

Structure and ceilings:
- Summary: keep at or below approximately {SUMMARY_MAX_WORDS} words; shorter is acceptable when the
  positioning is clear.
{_role_requirements()}

Important:
- Preferred bullet counts are not quotas.
- Section word counts are maximums, not targets.
- Do not add a bullet or extra wording merely to use available space.
- Return roles in the exact order shown above.
- Echo each role's company and role name exactly as defined in the evidence headers.

For each bullet, return:
- `text`: final resume bullet text without a bullet glyph
- `source_files`: exact relative path(s) supporting every claim

Return `summary_source_files` using exact relative paths as well.

Career-history evidence:
{career_history_context}

Job description:
{job_description}
"""


def build_fact_check_prompt(
    *, career_history_context: str, job_description: str, draft: TailoredResume
) -> str:
    """Build the independent grounding, evidence-selection, and job-fit repair request."""

    draft_json = json.dumps(draft.model_dump(), indent=2, ensure_ascii=True)

    return f"""Audit and improve this generated resume draft.

Independently infer the employer's selection thesis and reconsider whether the selected
accomplishments are the strongest available evidence for it. Do not limit the review to claim
verification.

Structure and ceilings:
- Summary: keep at or below approximately {SUMMARY_MAX_WORDS} words; shorter is acceptable.
{_role_requirements()}

Preferred bullet counts are not quotas and word counts are ceilings, not targets. Remove weak or
redundant bullets when allowed rather than padding them or preserving them to fill space.

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
    """Build a request to recover enough space to meet the hard two-page maximum."""

    tailored_json = json.dumps(tailoring.model_dump(), indent=2, ensure_ascii=True)

    return f"""Shorten this resume enough to fit within two rendered pages.

Preserve the selected bullets, their order, the positioning, and the main target-relevant proof in
each bullet. Remove lower-value words and secondary detail before weakening substantive evidence.

Compression attempt: {attempt}
Extracted text beyond page 2: {overflow_word_count} words

Overflow text:
{overflow_text or "[no extractable text]"}

The current bullet counts are already selected and valid. Do not add or remove bullets during the
compression pass.

Return only:
- `summary`
- `roles`, each containing `role_id` and its ordered `bullets` as strings

Resume to shorten:
{tailored_json}
"""
