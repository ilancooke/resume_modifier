# Resume Modifier

Local Python CLI that drafts a job-specific resume from a structured Markdown career history, fills a formatted Word template, exports it to PDF with Microsoft Word for Mac, enforces a two-page limit, and evaluates the finished resume against the job description.

## Workflow

For each run, the tool:

1. Recursively reads the configured career-history folder.
2. Assigns each Markdown file to a fixed resume role from its relative path.
3. Excludes the root `Template.md` questionnaire from model context.
4. Asks OpenAI to select the strongest relevant projects and draft the summary and bullets.
5. Runs a separate OpenAI factuality pass against the original Markdown evidence.
6. Enforces exact role, bullet-count, and source-attribution requirements locally.
7. Copies the resume stub and replaces its placeholders without rewriting other DOCX package parts.
8. Exports the result to PDF through Microsoft Word.
9. Conservatively compresses the content when necessary to meet the hard two-page limit.
10. Prints a match report for the tailored resume only.

The generated structure is fixed:

- Summary: approximately 50 words
- Amazon Data Scientist: exactly 6 bullets, approximately 400 words total
- Amazon Business Analyst / Business Intelligence Engineer: exactly 3 bullets, approximately 100 words total
- PetSmart Manager, Business Analytics: exactly 2 bullets, approximately 80 words total
- PetSmart Customer Insights Analyst: exactly 1 bullet, approximately 50 words total

Exact bullet counts, factual accuracy, and the two-page limit are hard requirements. Word totals are soft targets and may be reduced to make the resume fit cleanly.

## Requirements

- Python 3.11+
- macOS with Microsoft Word installed in `/Applications` or `~/Applications`
- `OPENAI_API_KEY` set in the environment or `.env`

The first PDF export may cause macOS to request permission for the terminal or Python launcher to control Microsoft Word. Enable it under **System Settings > Privacy & Security > Automation**. Word must be installed and activated, but it does not need to be open before running the CLI.

## Installation

```bash
source .venv/bin/activate
python -m pip install -e .
```

Create `.env` in the project root:

```dotenv
OPENAI_API_KEY=your_api_key_here
```

## Configuration

Edit `config.json`:

```json
{
  "resume_template_path": "/Users/ilan/Documents/resumes/stub/ilan_cooke_resume_stub.docx",
  "career_history_dir": "/Users/ilan/Documents/ilan vault/career history",
  "job_description_path": "/Users/ilan/Documents/resumes/job_description/role.txt",
  "tailored_resumes_dir": "/Users/ilan/Documents/resumes/roles"
}
```

Career-history assignments are derived from these relative paths:

- `Amazon/Data Scientist/**/*.md`
- `Amazon/BA-BIE/**/*.md`
- `Petsmart/PetSmart - Manager, Business Analytics.md`
- `Petsmart/PetSmart - Customer Insights Analyst.md`

An unrecognized Markdown path is treated as an error so new evidence is not silently assigned to the wrong role.

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

- `--dry-run`: draft and validate content without writing DOCX or PDF files
- `--show-diff`: show the placeholder-to-content preview
- `--model`: choose the OpenAI model; default is `gpt-5.6-sol`
- `--log-level`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`

## Outputs

The company and job ID are extracted from the job description:

```text
<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.docx
<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.pdf
```

The source template is never modified.

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
