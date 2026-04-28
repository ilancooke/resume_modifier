# Resume Modifier

Local Python CLI for tailoring a base resume to a job description, preserving the original `.docx` formatting, exporting the tailored result to PDF through Apple Pages on macOS, and appending an entry to a job application tracker workbook.

## What It Does

For each run, the tool:

1. Reads a base resume from `.docx`
2. Reads a job description from raw text
3. Uses the OpenAI API with strict structured output to:
   - rewrite the summary
   - rewrite bullets for each role
   - reorder bullets within each role
   - extract tracker metadata from the job description
4. Updates the copied `.docx` in place without recreating the document
5. Exports the tailored resume to PDF using Apple Pages
6. Appends a row to the Excel job application tracker

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
./.venv/bin/python -m pip install openai pydantic python-dotenv python-docx openpyxl
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
  "tailored_resumes_dir": "/Users/ilan/Documents/resumes/roles",
  "job_application_tracker_path": "/Users/ilan/Documents/resumes/job_application_tracker.xlsx"
}
```

### Config Fields

- `base_resume_path`: default input resume `.docx`
- `job_description_path`: default input job description `.txt`
- `tailored_resumes_dir`: base directory where tailored resumes are written
- `job_application_tracker_path`: Excel workbook updated after each successful resume generation

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

- `<tailored_resumes_dir>/<company>/<candidate>_<company>_<role_number>.docx`
- `<tailored_resumes_dir>/<company>/<candidate>_<company>_<role_number>.pdf`

Example:

- `/Users/ilan/Documents/resumes/roles/synchrony/ilan_cooke_synchrony_2600857.docx`
- `/Users/ilan/Documents/resumes/roles/synchrony/ilan_cooke_synchrony_2600857.pdf`

The candidate name is derived from the resume header. The company and role number used in the output path come from the structured model output and fall back to `unknown` when unavailable.

## Job Tracker Behavior

After a successful non-dry-run execution, the tool appends a row to the workbook at `job_application_tracker_path`.

If the tracker file does not exist yet, the tool creates a new `.xlsx` workbook with these headers:

- `Company Name`
- `Role Title`
- `Location`
- `DS role type`
- `Alignment strength with my background`
- `Salary Range`
- `Job ID`
- `Date Applied`
- `Comments`
- `Status`

### How Tracker Fields Are Filled

- `Company Name`: extracted from the job description, otherwise `unknown`
- `Role Title`: extracted from the job description, otherwise `unknown`
- `Location`: extracted from the job description, including `remote` or `hybrid` wording when present, otherwise `unknown`
- `DS role type`: one primary category chosen by the model from:
  - `Product / Experimentation DS`
  - `Applied ML`
  - `Machine Learning/Predictive model building`
  - `ML Engineering`
  - `AI / LLM / Agentic`
  - `Analytics / BI`
  - `Other`
- `Alignment strength with my background`: one honest sentence generated from the resume and job description
- `Salary Range`: extracted from the job description, otherwise `unknown`
- `Job ID`: extracted from the job description, otherwise `unknown`
- `Date Applied`: left blank for manual entry
- `Comments`: left blank for manual entry
- `Status`: left blank for manual entry

The tracker updater maps values by header name, so reordering columns in the workbook is supported as long as the same headers are still present.

## OpenAI Model

The default model is `gpt-5.4-mini`.

You can override it:

```bash
python tailor_resume.py --model gpt-5.4-mini
```

## Useful Flags

- `--dry-run`: generate and validate output without writing the DOCX, PDF, or tracker row
- `--show-diff`: print a unified diff of the summary and bullet changes
- `--log-level`: set logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

Examples:

```bash
python tailor_resume.py --dry-run
python tailor_resume.py --show-diff
python tailor_resume.py --log-level DEBUG
```

## Notes

- The tool modifies a copied resume, not the source resume.
- Formatting is preserved by editing the existing `.docx` structure rather than rebuilding the document.
- PDF export depends on Apple Pages and only works on macOS.
