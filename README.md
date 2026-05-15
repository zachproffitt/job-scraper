# Builder Jobs — Scraper

Hourly pipeline that scrapes engineering jobs from company career pages, classifies each role with Claude, and publishes rendered markdown to **[zachproffitt/builder-jobs](https://github.com/zachproffitt/builder-jobs)**.

<sub>Last updated May 15, 2026 at 21:37 UTC</sub>

## Pipeline

```
fetch_jobs.py              fetch current listings from all companies
fetch_job_descriptions.py  fetch full description text for new jobs
classify_companies.py      generate company summaries via Claude
classify_jobs.py           classify roles, summarize, extract skills and comp
render_jobs.py             write one .md per engineering role → builder-jobs/jobs/
generate_index.py          regenerate README.md in builder-jobs
```

## Actions

| Workflow | Schedule | What it does |
|---|---|---|
| **Jobs** | Hourly | Fetch listings → classify → render → publish to builder-jobs |
| **Companies** | Sundays | Discover new companies from YC, VC portfolios, and industry curation → detect ATS |

Both workflows write a step summary visible in the Actions dashboard. Logs are committed with each run (`data/pipeline.log`, `data/discovery.log`).

Runs hourly via GitHub Actions. Commits to both repos automatically.

## Classification

Each job is sent to Claude with a structured prompt that extracts:

- **BUILDER** — does this role primarily write code?
- **SUMMARY** — 1–2 sentence description
- **SKILLS** — up to 8 specific technologies, languages, or tools
- **LEVEL** — intern / junior / mid / senior / staff / principal / manager
- **COMP** — base salary range with original currency symbol
- **HYBRID / CONTRACT** — work arrangement flags
- **REGION** — us / canada / international / unclear

Non-engineering, contract, and international (outside US/Canada) roles are filtered out.

## Supported ATS

| ATS | Companies | Scraper |
|---|---|---|
| Ashby | 415 | `scrapers/ats_ashby.py` |
| Greenhouse | 270 | `scrapers/ats_greenhouse.py` |
| Lever | 82 | `scrapers/ats_lever.py` |
| Workday | 54 | `scrapers/ats_workday.py` |
| BambooHR | 26 | `scrapers/ats_bamboo.py` |
| Breezy | 21 | `scrapers/ats_breezy.py` |
| Workable | 22 | `scrapers/ats_workable.py` |
| SmartRecruiters | 4 | `scrapers/ats_smartrecruiters.py` |
| Eightfold | 2 | `scrapers/ats_eightfold.py` |
| **Total** | **896** | |

## Company sources

~5,700 candidates in `data/company_names.txt`, sourced from:

- **Y Combinator** — all active batches via Algolia (`discovery/discover_yc_companies.py`)
- **VC portfolios** — Founders Fund, Khosla Ventures, Greylock, Sequoia (`discovery/discover_vc_companies.py`)
- **Industry curation** — Claude-enumerated top companies across 20+ sectors (`discovery/discover_industry_companies.py`)

ATS detection (`discovery/discover_companies.py`) runs over the candidate list and populates `data/companies.json` with confirmed companies and their slugs.

> *More ATS scrapers and company sources are actively being added.*

## Rolling window

Jobs older than **14 days** are dropped and their `.md` files deleted. `seen_jobs.json` is a permanent ID registry — prevents re-surfacing long-running postings that age out and reappear.

New companies are archived on first fetch so their existing backlog doesn't flood the board. Only jobs that appear on subsequent runs are treated as new.

## Setup

Requires Python 3.11+. Clone both repos as siblings:

```bash
git clone https://github.com/zachproffitt/builder-jobs-scraper
git clone https://github.com/zachproffitt/builder-jobs
```

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
```

Run the full pipeline:

```bash
PYTHONPATH=. python pipeline/fetch_jobs.py
PYTHONPATH=. python pipeline/fetch_job_descriptions.py
PYTHONPATH=. python pipeline/classify_companies.py
PYTHONPATH=. python pipeline/classify_jobs.py
PYTHONPATH=. python pipeline/render_jobs.py ../jobs/jobs
PYTHONPATH=. python pipeline/generate_index.py ../jobs
```

## Data files

| File | Description |
|---|---|
| `data/company_names.txt` | ~5,700 candidate companies: name \| domain |
| `data/companies.json` | Confirmed companies with detected ATS and slug |
| `data/seen_jobs.json` | Permanent ID registry: `{job_id: first_seen_date}` |
| `data/seen_companies.json` | First-fetch registry per company |
| `data/jobs_raw.json` | Rolling 14-day window of listings with descriptions (not committed) |
| `data/jobs_classified.json` | Claude inference results per job ID |
| `data/companies_classified.json` | Company summaries used in rendered output |
| `data/pipeline.log` | Log for the Jobs workflow (cleared each run) |
| `data/discovery.log` | Log for the Companies workflow (cleared each run) |
