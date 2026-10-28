# Resume Modifier

Local Python CLI that drafts a job-specific resume from a structured Markdown career history, fills a formatted Word template, exports it to PDF with Microsoft Word for Mac, enforces a two-page limit, and evaluates the finished resume against the job description.

## Workflow

For each run, the tool:

1. Recursively reads the configured career-history folder.
2. Assigns each Markdown file to a fixed resume role from its relative path.
3. Excludes the root `Template.md` questionnaire from model context.
4. Asks OpenAI to select the strongest relevant projects and draft the summary and bullets.
5. Runs a separate OpenAI factuality pass against the original Markdown evidence.
6. Enforces role identity, maximum bullet counts, and source-attribution requirements locally.
7. Copies the resume stub and replaces its placeholders without rewriting other DOCX package parts.
8. Exports the result to PDF through Microsoft Word.
9. Conservatively compresses the content when necessary to meet the hard two-page limit.
10. Evaluates the rendered PDF against the job description.
11. Uses the match report and full career history for one targeted revision, when feedback exists.
12. Independently fact-checks changed content against the full career history.
13. Renders the candidate, enforces two pages, and evaluates the revised PDF once.
14. Publishes the validated revision and saves the initial and final reports plus revision decisions.

The feedback cycle is enabled by default. It searches all career-history files, including omitted
details in files already cited, and can expand, add, or replace bullets or refine the summary.
Each feedback item receives a decision: addressed, already demonstrated, unsupported, outside the
editable template sections, or deferred with a reason. Unsupported experience is not added.

A changed resume normally adds three API calls: targeted revision, factuality review, and final PDF
matching, plus compression calls if needed. Both revision and factuality review receive the full
career history; matching receives only extracted PDF text and the job description. Compression
receives the current resume, overflow information, and the supported repair objectives. There is no
repeat revision loop and no automatic selection based solely on a higher numerical match score.

When no feedback exists, no additional model calls are made. When the proposed resume is unchanged,
the factuality, export, and final-match stages are skipped. If the factuality reviewer restores the
original resume, export and final matching are skipped. Revision failures retain the initial files,
are recorded in diagnostics, and cause a nonzero exit status.

The generated structure prioritizes the strongest nonredundant evidence:

- Summary: up to approximately 45 words
- Amazon Data Scientist: prefer about 7 bullets; maximum 8; approximately 260 words or fewer
- Amazon Business Analyst / Business Intelligence Engineer: prefer about 1 bullet; maximum 2;
  approximately 70 words or fewer
- PetSmart combined Manager, Business Analytics / Customer Insights Analyst: prefer about 3 bullets;
  maximum 4; approximately 100 words or fewer, drawn from either or both roles' source files

The two-page limit, factuality, correct role history, and maximum bullet counts are hard constraints.
Preferred bullet counts and word ceilings are guides, not quotas: the workflow may use fewer bullets
when another accomplishment would not strengthen the resume.

Most bullets should be 25-40 words. For Amazon Data Scientist, prefer 30-40 words when supported
detail strengthens the evidence, allowing roughly 45 words when substantive context warrants it.
For other roles, prefer 25-35 words when sufficient; shorter bullets remain acceptable. Preserve
useful methods, operational responsibilities, collaboration, and outcomes without adding filler.

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
  "career_history_dir": "/Users/ilan/Documents/ilan vault/career history",
  "job_description_path": "/Users/ilan/Documents/resumes/job_description/role.txt",
  "tailored_resumes_dir": "/Users/ilan/Documents/resumes/roles",
  "resume_diagnostics_dir": "/Users/ilan/Documents/resumes/resume_diagnostics"
}
```

Career-history assignments are derived from these relative paths:

- `Amazon/Data Scientist/**/*.md`
- `Amazon/BA-BIE/**/*.md`
- `Petsmart/PetSmart - Manager, Business Analytics.md`
- `Petsmart/PetSmart - Customer Insights Analyst.md`

An unrecognized Markdown path is treated as an error so new evidence is not silently assigned to the wrong role.
Both PetSmart files are assigned to the shared `petsmart_analytics` section; source attribution
continues to identify the individual supporting files.

The DOCX template must contain one `[SUMMARY GOES HERE]` paragraph and three
`[BULLET POINTS GO HERE]` paragraphs, in this order:

1. Amazon Data Scientist, Marketplace Fraud & Abuse.
2. Amazon Business Analyst / BI Engineer, Related Accounts Enforcement.
3. PetSmart, with both Manager, Business Analytics and Customer Insights Analyst titles above one
   shared bullet placeholder.

Skills, Selected Projects, and other content outside these placeholders are preserved.

## Usage

Standard run:

```bash
python tailor_resume.py
```

Override inputs:

```bash
python tailor_resume.py \
  --template /path/to/resume_stub.docx \
  --career-history /path/to/career_history \
  --jd /path/to/job_description.txt
```

Useful options:

- `--dry-run`: draft and fact-check without files, PDF matching, or feedback revision
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

For an attempted feedback revision, the diagnostics link to a unique archive under:

```text
<resume_diagnostics_dir>/<candidate>_<company>_<job_id>.diagnostics.feedback_runs/<run>/
```

It retains the initial DOCX/PDF, attributed content and structured match report, proposal and audit
when available, staged candidate files, candidate match report, and outcome. The main diagnostics
show feedback decisions and sources, the initial report, the actual initial-to-final content diff,
score differences, and the selected PDF's final report. Decisions describe the proposal/audit;
remaining weak signals must be read from the final PDF report because compression may affect wording.
Candidate files replace the standard outputs only after export, page fitting, and final matching
succeed. Initial files are restored if publication fails partway through.

Standalone matching and experiment treatments retain their existing behavior; they do not invoke
the standard CLI's additional feedback cycle.

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
