# Resume Modifier

Local Python CLI that selects job-specific accomplishments from an approved bullet library,
fills a formatted Word template, exports it to PDF with Microsoft Word for Mac, enforces a two-page
limit, and evaluates the finished resume against the job description.

## Workflow

For each standard CLI run, the tool:

1. Loads the job description, template, and approved Markdown bullet library.
2. Makes its **first content decision**: keep the complete Finbot Selected Projects section or
   remove it entirely. This independent call also classifies the job as Applied ML / Data Science,
   AI Engineering, or Hybrid and identifies its 3–5 priority requirements.
3. **Locks the Finbot decision for the entire run.** Later selection, review, page fitting, and
   feedback cannot reverse it, reduce the section, or edit its wording. A new run makes a new decision.
4. Selects approved experience bullet IDs, ranks up to ten further relevant reserve bullets, and
   writes a supported summary of at most 45 words.
5. Independently reviews the selection, summary factuality, relevance, redundancy, and project
   exceptions against the complete approved library and fixed template evidence.
6. Locally validates the IDs, correct role attribution, project limits, and evidence references.
   Experience text is retrieved directly from the library and reused **verbatim**; no model supplies
   replacement bullet text. Skills, employment titles and dates, education, and certifications stay fixed.
7. Fills the Word placeholders, retains or removes the complete Finbot block, and exports through Word.
8. Checks the actual PDF against the hard two-page limit. Overflow removes the lowest-priority
   eligible approved bullet and re-renders until it fits or no valid reduction remains. It never
   rewrites bullets, changes Skills or Finbot, shrinks fonts, or adds filler. Once the draft fits, it
   tries the pre-reviewed reserves in rank order, adding only bullets that also fit within two pages.
   Rejected trials preserve the last valid draft; shorter later reserves can still be tried.
9. Evaluates the rendered PDF against the job description.
10. When feedback exists, performs at most one targeted selection revision and an independent review
    of changed content, under the same locked decision and approved-library constraints.
11. Renders and page-fits a changed candidate, evaluates its PDF once, and only then publishes it.
12. Prints a concise tailoring summary and saves the final selections, provenance, decisions,
    initial/final reports, page-fitting changes, and content diff in diagnostics and a unique archive.

The model normally selects one bullet per project. A second is allowed for substantially different,
highly relevant accomplishments and must have a recorded justification. Individual projects cannot
supply more than two. Explicit cross-project categories, currently **MLOps and Operational Excellence**
and **Production Feature Engineering Examples**, may supply multiple distinct accomplishments.

Amazon Data Scientist receives the most experience space. With Finbot kept, initial soft guidance is
about 6–7 Data Scientist bullets; with it removed, about 9–11 when the additional accomplishments
strengthen the application. These are starting budgets; additional relevant reserves can be admitted
when the rendered layout has room. There is no fixed eight-bullet Data Scientist ceiling.
BA/BIE allows at most two bullets and the shared PetSmart section at most four. These are limits,
not quotas; final selection depends on relevance and actual pagination. Approved text is never
rewritten to meet a word target.

The initial content stages use three model calls: Finbot relevance, selection/summary, and independent
review. PDF matching adds one call. A changed feedback revision normally adds three calls: selection,
independent review, and final PDF matching. A locally invalid selection (for example, feedback citing
a removed bullet) receives at most one corrective call; a second failure retains the normal failure
behavior. Invalid parsed selections and validation errors are archived in `validation_attempts.json`.
Page fitting, including reserve trials, makes no API calls. No feedback means no
revision calls; unchanged proposals skip review, export, and final matching. There is no repeated
revision loop and no automatic rejection solely because a numerical match score decreases.

Each feedback item receives an evidenced decision: addressed, already demonstrated, unsupported,
outside the editable scope, or deferred with a reason. Requests to change Skills or the locked
Finbot inclusion are outside scope. Unsupported experience cannot be added. Revision failures keep
the initial files, are recorded in diagnostics, and return a nonzero exit status.

## Requirements

- Python 3.11+
- macOS with Microsoft Word installed in `/Applications` or `~/Applications`
- `OPENAI_API_KEY` set in the environment or `.env`
- `ANTHROPIC_API_KEY` set when running experiments with Claude generation or judges

The first PDF export may cause macOS to request permission for the terminal or Python launcher to control Microsoft Word. Enable it under **System Settings > Privacy & Security > Automation**. Word must be installed and activated, but it does not need to be open before running the CLI.

PDF export stages a temporary DOCX and PDF inside Microsoft Word's sandbox container, then atomically
copies the PDF to its requested destination. Each staging directory is removed after the attempt,
and abandoned `resume_modifier/export_*` directories older than 24 hours are removed before a later
export. Final DOCX and PDF paths are unchanged.

## Installation

```bash
source .venv/bin/activate
python -m pip install -e .
```

Create `.env` in the project root:

```dotenv
OPENAI_API_KEY=your_api_key_here
ANTHROPIC_API_KEY=your_api_key_here
```

## Configuration

Edit `config.json`:

```json
{
  "resume_template_path": "/Users/ilan/Documents/resumes/stub/ilan_cooke_resume_stub.docx",
  "bullet_library_dir": "/Users/ilan/Documents/ilan vault/career history bullet points",
  "job_description_path": "/Users/ilan/Documents/resumes/job_description/role.txt",
  "tailored_resumes_dir": "/Users/ilan/Documents/resumes/roles",
  "resume_diagnostics_dir": "/Users/ilan/Documents/resumes/resume_diagnostics"
}
```

Approved-library assignments are derived from these relative paths:

- `Amazon/Data Scientist/**/*.md`
- `Amazon/BA-BIE/**/*.md`
- `Petsmart/PetSmart_Bullets.md`

Each file contains a title, an optional `Candidate resume bullets.` introduction, project/role
headings, and complete approved bullets, one per line:

```markdown
# Amazon — Data Scientist

Candidate resume bullets.

## Relationship Risk Model

- **ADS-REL-01:** Approved resume-ready accomplishment, preserved verbatim.
```

IDs must be globally unique and use `ADS-` for Amazon Data Scientist, `ABA-` for Amazon BA/BIE,
and `PM-` or `PC-` for the shared PetSmart section. Malformed entries, unknown file-role paths,
duplicate IDs, empty files, and missing role libraries are errors. Project headings define selection
groups. PetSmart's Manager and Customer Insights Analyst headings preserve the supporting subrole.
Do not store narrative career-history files in this directory.

`career_history_dir`, if retained in `config.json`, is optional legacy configuration and is not read
by the standard tailoring workflow. The experiment runner continues to use its own YAML-configured
narrative career-history inputs and original generation/compression pipeline.

The DOCX template must contain one `[SUMMARY GOES HERE]` paragraph and three
`[BULLET POINTS GO HERE]` paragraphs, in this order:

1. Amazon Data Scientist, Marketplace Fraud & Abuse.
2. Amazon Business Analyst / BI Engineer, Related Accounts Enforcement.
3. PetSmart, with both Manager, Business Analytics and Customer Insights Analyst titles above one
   shared bullet placeholder.

The optional Finbot block must start with `SELECTED PROJECTS`, contain a `Finbot:` project title,
and end immediately before `WORK EXPERIENCE`. Its heading, title, and all three existing bullets
are kept together or removed together. Skills and other fixed template content are preserved.
The source DOCX and bullet-library files are never modified.

## Usage

Standard run:

```bash
python tailor_resume.py
```

Override inputs:

```bash
python tailor_resume.py \
  --template /path/to/resume_stub.docx \
  --bullet-library /path/to/approved_bullets \
  --jd /path/to/job_description.txt
```

Useful options:

- `--dry-run`: decide Finbot, select and independently review bullets, and print the summary/content
  preview without writing files, exporting PDFs, matching, or feedback revision (three API calls)
- `--skip-feedback-revision`: retain the initial resume and its match report without the extra cycle
- `--show-diff`: show the placeholder-to-content preview
- `--model`: choose the OpenAI model; default is `gpt-6.1-terra`
- `--log-level`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`

## Outputs

The company and job ID are extracted from the job description:

```text
<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.docx
<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.pdf
```

Each completed run also writes its evidence-attribution, optional diff, and tailored-match report to:

```text
<resume_diagnostics_dir>/<candidate>_<company>_<job_id>.diagnostics.txt
```

The source template is never modified.

Each non-dry run links its diagnostics to a unique selection archive under:

```text
<resume_diagnostics_dir>/<candidate>_<company>_<job_id>.diagnostics.selection_runs/<run>/
```

The archive retains the immutable plan, approved-library snapshot, job description, template hash,
draft and reviewed selections, fitted initial DOCX/PDF and match report, feedback signals, proposal
and audit when available, candidate files/report, and outcome. The main diagnostics describe the
final selected IDs, their source files and project headings, initial and final reports, score change,
page-fitting removals, reserve additions/deferrals, and actual initial-to-final content diff. Feedback decisions describe the
proposal/audit; read the final PDF report for remaining gaps because fitting may remove selections.
Candidate files replace the standard outputs only after export, page fitting, and final matching
succeed. Both previous outputs are restored if publication fails partway through.

Standalone matching and experiment treatments retain their existing behavior. Experiments still
measure narrative generation treatments; they do not invoke this approved-selection workflow or
its additional feedback cycle.

## Local tests

```bash
python -m unittest discover -s tests -q
```

Tests mock model calls and Word export. They verify selection constraints, immutable Finbot policy,
verbatim bullet reuse, fixed Skills/retained-project XML, page-fitting progress, and failure recovery
without API charges. Real pagination requires a separate Word export on macOS.

## Standalone match validation

Evaluate one PDF without editing it:

```bash
python validate_resume_match.py --resume /path/to/resume.pdf --jd /path/to/role.txt
```

Batch mode:

```bash
python validate_resume_match.py \
  --resume-dir /path/to/resume_folder \
  --jd /path/to/role.txt \
  --output-dir /path/to/match_reports
```

The validator scores seven dimensions out of 10, for a total of 70:

- Required qualifications match
- Core responsibility alignment
- Relevant skills and capabilities
- Industry, domain, and context fit
- Seniority and scope fit
- Evidence strength
- Communication and discoverability

PDF text extraction uses PyMuPDF. OCR is not implemented for scanned or image-only resumes.

## Resume experiments

The experiment runner compares complete generation treatments. A treatment can vary the career-
history context, generation model, or model settings while the rest of the pipeline remains fixed.
The prompt version is recorded and hashed; the current implementation accepts the `current` prompt
version. The included experiment compares the free-flow and transformed career-history collections
with the same generation model.

### Prepare job descriptions

Create the directory configured by `job_descriptions_dir` and place each posting in a separate
`.txt` file. The runner searches recursively. The filename and relative path become the stable
experiment case identifier, so a job ID inside the posting is optional.

By default every `.txt` file is included. To run a pilot against a fixed subset, add paths relative
to `job_descriptions_dir`:

```yaml
job_files:
  - role1.txt
  - role2.txt
  - nested/role5.txt
```

The included configuration uses the existing job-description directory:

```text
/Users/ilan/Documents/resumes/job_description/
  role-one.txt
  role-two.txt
```

### Validate before spending API credits

Install the updated dependencies, then validate local inputs and print the planned call counts:

```bash
python -m pip install -e .
python resume_experiment.py validate \
  --config experiments/context_ab/experiment.yaml
```

Optionally verify that both API credentials can access every configured model:

```bash
python resume_experiment.py check-models \
  --config experiments/context_ab/experiment.yaml
```

OpenAI models normally do not require a separate per-model opt-in. A project can, however, have an
allowlist or denylist. `check-models` reports any inaccessible or misspelled model before generation.
Anthropic judges default to `anthropic_thinking: disabled` so their output-token budget is reserved
for the structured comparison rather than hidden reasoning. Set it to `adaptive` only with a larger
`max_output_tokens` budget.

Anthropic generation treatments must set `temperature: null`. Claude Sonnet 5 rejects non-default
sampling parameters. Set `anthropic_thinking: adaptive` and use `reasoning_effort` to control the
quality, latency, and token-cost tradeoff.

### Run or resume the experiment

Run generation, blinded evaluation, and aggregation:

```bash
python resume_experiment.py run \
  --config experiments/context_ab/experiment.yaml
```

The command prints the new run directory. If a full run stops during generation or evaluation,
resume it with the same `run` command and the retained directory:

```bash
python resume_experiment.py run \
  --config experiments/context_ab/experiment.yaml \
  --run-dir experiment_runs/career_context_structure_v1_YYYYMMDDTHHMMSSZ
```

You can also resume or rerun individual stages by passing that directory:

```bash
python resume_experiment.py evaluate \
  --config experiments/context_ab/experiment.yaml \
  --run-dir experiment_runs/career_context_structure_v1_YYYYMMDDTHHMMSSZ

python resume_experiment.py report \
  --config experiments/context_ab/experiment.yaml \
  --run-dir experiment_runs/career_context_structure_v1_YYYYMMDDTHHMMSSZ
```

Completed generation and evaluation artifacts are skipped automatically, so do not add `--force`
when recovering from an ordinary interruption or validation failure. A run refuses to continue
if its configuration, job descriptions, resume template, or career-history files changed. Start a
new run when an input changes. `--force` intentionally reruns completed artifacts for the selected
stage.

Run artifacts are stored under the ignored `experiment_runs/` directory. Each run retains input
hashes, a job-description snapshot, generation intermediates, final DOCX/PDF files, extracted PDF
text, normalized token usage, request latency, estimated list-price cost, raw judgments, flat CSVs,
and job-level JSON and Markdown summaries. When `absolute_scoring` is enabled, each resume is scored
independently before the secondary pairwise comparison. This avoids making an order-sensitive
pairwise vote the primary measurement.

### OpenAI versus Anthropic pilot

The included model-comparison pilot holds the context, prompts, template, and validation rules
constant while comparing GPT-5.6 Terra with Claude Sonnet 5. It selects five varied jobs and creates
three replicates per model, for 30 resumes total:

```bash
python resume_experiment.py validate \
  --config experiments/model_ab_pilot/experiment.yaml

python resume_experiment.py check-models \
  --config experiments/model_ab_pilot/experiment.yaml

python resume_experiment.py run \
  --config experiments/model_ab_pilot/experiment.yaml
```

The pilot stops at those five jobs because `job_files` is an explicit allowlist. It uses independent
absolute scoring as the primary evidence and order-balanced pairwise scoring as a secondary check.
Review the generated `reports/summary.md`, `reports/absolute_scores.csv`, and
`reports/judgments.csv` before deciding whether to create a follow-up configuration for the remaining
jobs.

The completed pilot found low variation across three generations, while 60% of matched pairwise
decisions changed when resume order changed. For cost-conscious follow-ups, prefer broader job
coverage with one generation per treatment, keep two cross-provider absolute judges, and set
`pairwise_scoring: false`. Pairwise comparison remains available as a secondary diagnostic when its
additional cost and order sensitivity are acceptable.

### Create future configurations with an LLM

You do not need to write experiment YAML manually. Give an LLM the closest existing configuration:

- Use `experiments/context_ab/experiment.yaml` for context or prompt comparisons.
- Use `experiments/model_ab_pilot/experiment.yaml` for provider or model comparisons.

Then use this request:

```text
Create a new resume experiment configuration by copying the closest existing YAML configuration.
State the experimental hypothesis and change only the treatment fields needed to test it. Preserve
the job inputs, template, artifact root, unrelated treatment settings, cross-provider judges, and
pricing fields unless I explicitly request changes. Use independent absolute scoring as the primary
evaluation. For a cost-conscious pilot, use one generation per treatment and disable pairwise
scoring; enable order-balanced pairwise scoring only when requested. Use lowercase unique IDs
containing only letters, numbers, underscores, or hyphens. For Anthropic generation, set
temperature to null and specify the thinking mode and output-token budget. Explain the single
experimental factor that changes, write the YAML file under experiments/, and run only the local
validate command so I can review planned generation and judge-call counts. Do not run generation or
evaluation.
```

For a model comparison, keep `career_history_dir`, `prompt_version`, and the template constant while
changing `model` and any model-specific settings:

```yaml
treatments:
  - id: terra
    provider: openai
    model: gpt-5.6-terra
    reasoning_effort: none
    career_history_dir: /path/to/one/fixed/context
  - id: astra
    provider: openai
    model: gpt-6-astra
    reasoning_effort: low
    temperature: null
    career_history_dir: /path/to/one/fixed/context
```

Change one treatment factor at a time when the goal is a causal comparison. If several fields differ,
interpret the result as a comparison of complete workflows rather than of one isolated factor.
Pricing fields are optional, but cost totals are available only for calls whose configured model has
pricing information.
