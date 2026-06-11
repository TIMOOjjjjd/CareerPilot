# CareerPilot AI

CareerPilot AI is a personal job-matching agent. It reads your resume and job preferences, collects fresh job listings with Apify, ranks them with OpenAI, stores local JSON outputs, and emails you the best matches each day.

This is an MVP for personal use. It intentionally avoids a frontend, database, authentication system, and Docker setup.

## Features

- Loads user preferences from `config/config.yaml`
- Parses `resume/resume.pdf` into a cached structured candidate profile
- Collects jobs from an Apify actor
- Normalizes, deduplicates, and filters jobs before LLM scoring
- Scores jobs with the OpenAI API using only the structured resume profile
- Saves raw jobs, scored jobs, and email history under `data/`
- Sends an HTML email with the top ranked unseen jobs
- Supports local `.env` files and GitHub Actions secrets
- Runs manually with `python src/main.py`

## Architecture

```text
config/config.yaml
        |
resume/resume.pdf ----> resume_parser.py ----> data/resume_profile.json
        |
        v
collect_jobs.py ----> data/jobs_raw.json
        |
        v
preprocess_jobs.py
        |
        v
score_jobs.py ----> data/jobs_scored.json
        |
        v
storage.py ----> data/history.json
        |
        v
email_sender.py ----> Gmail SMTP
```

## Setup

### 1. Clone or fork

```bash
git clone <your-repo-url>
cd CareerPilot-AI
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Add environment variables

Copy `.env.example` to `.env` for local runs:

```bash
cp .env.example .env
```

Set these values:

```text
OPENAI_API_KEY=...
APIFY_TOKEN=...
EMAIL_ADDRESS=your_gmail_address@gmail.com
EMAIL_APP_PASSWORD=your_gmail_app_password
```

`EMAIL_APP_PASSWORD` should be a Gmail app password, not your normal Gmail password.

### 5. Add your resume

Place your PDF resume here:

```text
resume/resume.pdf
```

The file is ignored by Git by default.

### 6. Edit preferences

Open `config/config.yaml` and update:

- `profile.name`
- `profile.email`
- target roles and locations
- salary threshold
- visa sponsorship preference
- excluded keywords
- `sources.apify_actor_id`

You can set `job_preferences.roles` to `[]` if you want CareerPilot AI to infer target roles from your resume. If roles are provided, the configured values are used exactly as the search targets for the run.

Apify actors have different input schemas. This MVP sends common search fields such as roles, locations, queries, and max item counts. If your chosen actor needs custom input, add an optional `sources.apify_input` mapping in `config/config.yaml`; those values are merged into the request body.

### 7. Run locally

```bash
python src/main.py
```

Generated files are written to `data/`:

- `jobs_raw.json`
- `jobs_scored.json`
- `history.json`
- `resume_profile.json`

These files are ignored by Git.

## GitHub Actions

The workflow is defined in `.github/workflows/daily.yml`.

Add these repository secrets in GitHub:

- `OPENAI_API_KEY`
- `APIFY_TOKEN`
- `EMAIL_ADDRESS`
- `EMAIL_APP_PASSWORD`

Because `resume/resume.pdf` should not be committed, add this optional secret for scheduled runs:

- `RESUME_PDF_BASE64`

Create it from your PDF:

```bash
base64 -w 0 resume/resume.pdf
```

On macOS:

```bash
base64 -i resume/resume.pdf
```

On Windows PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("resume/resume.pdf"))
```

Paste the output into the `RESUME_PDF_BASE64` GitHub secret.

The workflow includes two UTC schedules and checks the current `Europe/London` hour before running, because GitHub cron does not support daylight-saving-aware time zones directly. It also supports manual runs with `workflow_dispatch`.

## Notes On Job Sources

This project uses Apify as the collection layer. You are responsible for choosing actors that comply with the relevant job board terms, Apify actor rules, and applicable laws. Do not use this project to bypass access controls, ignore robots or platform rules, or scrape sources you are not allowed to access.

## Repository Hygiene

The following are ignored by Git:

- `.env`
- `resume/*.pdf`
- generated `data/*.json`
- Python caches and virtual environments

Commit the code, config template, and workflow. Do not commit secrets, personal resumes, or generated job data.
