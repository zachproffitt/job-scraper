# Builder Jobs — Scraper

Hourly pipeline that scrapes engineering jobs from company career pages, classifies each role with Claude, and publishes a curated index to **[zachproffitt/builder-jobs](https://github.com/zachproffitt/builder-jobs)**.

<sub>Last updated May 21, 2026 at 13:22 UTC</sub>

## Pipeline

```
fetch_jobs.py              fetch current listings from all companies
fetch_job_descriptions.py  fetch full description text for new jobs
classify_companies.py      generate company summaries via Claude
classify_jobs.py           classify roles, summarize, extract skills and comp
render_job_indexes.py      regenerate README.md, REMOTE.md, COMPANIES.md in builder-jobs
```

## Actions

| Workflow | Schedule | What it does |
|---|---|---|
| **Jobs** | Hourly | Fetch listings → classify → publish index to builder-jobs |
| **Companies** | Sundays | Discover new companies from YC, VC portfolios, and industry curation → detect ATS |

Both workflows write a step summary visible in the Actions dashboard. Logs are committed with each run (`data/jobs.log`, `data/companies.log`).

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
| Ashby | 563 | `scrapers/ats_ashby.py` |
| Greenhouse | 551 | `scrapers/ats_greenhouse.py` |
| Lever | 176 | `scrapers/ats_lever.py` |
| Workday | 156 | `scrapers/ats_workday.py` |
| BambooHR | 69 | `scrapers/ats_bamboo.py` |
| Breezy | 51 | `scrapers/ats_breezy.py` |
| Workable | 67 | `scrapers/ats_workable.py` |
| SmartRecruiters | 11 | `scrapers/ats_smartrecruiters.py` |
| Eightfold | 6 | `scrapers/ats_eightfold.py` |
| **Total** | **1650** | |

## Company sources

~5,700 companies in `data/companies.json`, sourced from:

- **Y Combinator** — all active batches via Algolia (`pipeline/fetch_yc_companies.py`)
- **VC portfolios** — Founders Fund, Khosla Ventures, Greylock, Sequoia (`pipeline/fetch_vc_companies.py`)
- **Industry curation** — Claude-enumerated top companies across 20+ sectors (`pipeline/fetch_industry_companies.py`)

ATS detection (`pipeline/fetch_companies.py`) resolves any entry with `"status": "new"` and updates it in place.

**To add a company manually:** add a stub to `data/companies.json` and trigger the Companies workflow (or run `fetch_companies.py` locally):

```json
{"name": "Acme Corp", "website": "https://acme.com", "status": "new"}
```

**To remove a company:** set `"status": "inactive"` in `data/companies.json`. It will stop being fetched immediately and won't be re-added by discovery.

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
PYTHONPATH=. python pipeline/render_job_indexes.py ../jobs
```

## Data files

| File | Description |
|---|---|
| `data/companies.json` | All companies: name, website, ATS, slug, status |
| `data/jobs_seen.json` | Permanent ID registry: `{job_id: first_seen_timestamp}` |
| `data/companies_seen.json` | First-fetch registry per company |
| `data/jobs_raw.json` | Rolling 14-day window of listings with descriptions (not committed) |
| `data/jobs_classified.json` | Claude inference results per job ID |
| `data/companies_classified.json` | Company summaries used in rendered output |
| `data/jobs.log` | Log for the Jobs workflow (committed each run) |
| `data/companies.log` | Log for the Companies workflow (committed each run) |
