# Resume Modifier

Local Python CLI for tailoring a base resume to a job description, preserving the original `.docx` formatting, and exporting the tailored result to PDF through Apple Pages on macOS.

The package also includes a separate read-only workflow for rating how well a PDF resume matches a job description.

## What It Does

For each run, the tool:

1. Reads a base resume from `.docx`
2. Reads a job description from raw text
3. Uses two OpenAI API calls with strict structured output to:
   - rewrite the summary
   - rewrite bullets for each role in original source order
   - choose the final bullet order within each role
   - extract company name and job ID from the job description for output naming
4. Updates the copied `.docx` in place without recreating the document
5. Exports the tailored resume to PDF using Apple Pages

## Requirements

- Python 3.11+
- macOS with Apple Pages installed
- `OPENAI_API_KEY` set in the environment or in `.env`

## Install

If you are using the project virtualenv:

```bash
./.venv/bin/python -m pip install -e .
```

Or install dependencies directly into the existing venv:

```bash
./.venv/bin/python -m pip install openai pydantic python-dotenv python-docx pymupdf
```

For the resume match validation workflow, install the package dependencies so PyMuPDF is available:

```bash
./.venv/bin/python -m pip install -e .
```

## Environment

Create a `.env` file in the project root:

```dotenv
OPENAI_API_KEY=your_api_key_here
```

## Config

Edit [config.json](/Users/ilan/workspace/resume_modifier/config.json):

```json
{
  "base_resume_path": "/Users/ilan/Documents/resumes/base/ilan_cooke_resume.docx",
  "job_description_path": "/Users/ilan/Documents/resumes/job_description/role.txt",
  "tailored_resumes_dir": "/Users/ilan/Documents/resumes/roles"
}
```

### Config Fields

- `base_resume_path`: default input resume `.docx`
- `job_description_path`: default input job description `.txt`
- `tailored_resumes_dir`: base directory where tailored resumes are written

## Default Inputs

By default, the CLI reads the input files from `config.json`:

- resume: `/Users/ilan/Documents/resumes/base/ilan_cooke_resume.docx`
- job description: `/Users/ilan/Documents/resumes/job_description/role.txt`

So the standard command is:

```bash
python tailor_resume.py
```

You can still override either input for a one-off run:

```bash
python tailor_resume.py \
  --resume /path/to/base_resume.docx \
  --jd /path/to/role.txt
```

## Output Layout

The tool creates this structure automatically:

- `<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.docx`
- `<tailored_resumes_dir>/<company>/<candidate>_<company>_<job_id>.pdf`

Example:

- `/Users/ilan/Documents/resumes/roles/synchrony/ilan_cooke_synchrony_2600857.docx`
- `/Users/ilan/Documents/resumes/roles/synchrony/ilan_cooke_synchrony_2600857.pdf`

The candidate name is derived from the resume header. The company and job ID used in the output path come from the structured model output and fall back to `unknown` when unavailable.

## OpenAI Model

The default model is `gpt-5.4-mini`.

You can override it:

```bash
python tailor_resume.py --model gpt-5.4-mini
```

## Useful Flags

- `--dry-run`: generate and validate output without writing the DOCX or PDF
- `--show-diff`: print a unified diff of the summary and bullet changes
- `--log-level`: set logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

Examples:

```bash
python tailor_resume.py --dry-run
python tailor_resume.py --show-diff
python tailor_resume.py --log-level DEBUG
```

## Resume Match Validation

Run this separate read-only workflow to rate how well a PDF resume matches a job description:

```bash
./.venv/bin/python validate_resume_match.py \
  --resume /path/to/resume.pdf \
  --jd /path/to/role.txt
```

If `--jd` is omitted, the workflow uses `job_description_path` from `config.json`.

The validation workflow:

1. Extracts native text from the resume PDF with PyMuPDF
2. Uses the OpenAI API with strict structured output to evaluate the match
3. Prints an overall score, dimension scores, strengths, gaps, knockout risks, and recommended resume changes
4. Does not edit the resume or export a PDF

Optional JSON output:

```bash
./.venv/bin/python validate_resume_match.py \
  --resume /path/to/resume.pdf \
  --jd /path/to/role.txt \
  --output-json /path/to/match_report.json
```

### Batch Match Validation

Run the validator against every PDF resume in a folder:

```bash
./.venv/bin/python validate_resume_match.py \
  --resume-dir /path/to/resume_folder \
  --jd /path/to/role.txt \
  --output-dir /path/to/match_reports
```

Batch mode writes:

- one `*.match.json` report per resume
- `summary.csv`

The summary CSV is sorted by `overall_score` descending, with failed resumes at the bottom. It includes the resume file, resume path, status, error, total score, rating label, each dimension score, and the final summary as the last column.

### Match Rating Dimensions

The score is job-type agnostic and totals 100 points:

- `Required qualifications match`: 25
- `Core responsibility alignment`: 25
- `Relevant skills and capabilities`: 15
- `Industry, domain, and context fit`: 10
- `Seniority and scope fit`: 10
- `Evidence strength`: 10
- `Communication and discoverability`: 5

The workflow uses native PDF text extraction first. OCR is not implemented yet; if a resume is scanned or image-only, the command reports that the extracted text is likely incomplete.

## Notes

- The tool modifies a copied resume, not the source resume.
- Formatting is preserved by editing the existing `.docx` structure rather than rebuilding the document.
- PDF export depends on Apple Pages and only works on macOS.
