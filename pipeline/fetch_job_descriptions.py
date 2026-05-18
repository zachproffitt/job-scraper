#!/usr/bin/env python3
"""
Fetch descriptions for jobs that don't have one yet.

Greenhouse:  per-job API endpoint
BambooHR:    per-job detail endpoint
Breezy:      job page HTML
Workable:    job page HTML
Workday:     per-job CXS detail endpoint
Eightfold:   per-position pcsx API

By default only processes jobs first_seen today.
Use --all to backfill all jobs without descriptions.

Usage:
    python fetch_job_descriptions.py          # today's new jobs only
    python fetch_job_descriptions.py --all    # all without descriptions
"""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scrapers._base import html_to_text

JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
WORKERS = 10

GREENHOUSE_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
BAMBOO_DETAIL_URL = "https://{slug}.bamboohr.com/careers/{job_id}/detail"


def fetch_greenhouse(job: dict, client: httpx.Client) -> str | None:
    slug = job.get("company_slug", "")
    job_id = job["id"].rsplit("-", 1)[-1]
    url = GREENHOUSE_DETAIL_URL.format(slug=slug, job_id=job_id)
    try:
        r = client.get(url, timeout=15)
        r.raise_for_status()
        content = r.json().get("content", "")
        return html_to_text(content) if content else None
    except Exception:
        return None


def fetch_bamboo(job: dict, client: httpx.Client) -> str | None:
    slug = job.get("company_slug", "")
    job_id = job["id"].rsplit("-", 1)[-1]
    url = BAMBOO_DETAIL_URL.format(slug=slug, job_id=job_id)
    try:
        r = client.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        description = r.json()["result"]["jobOpening"].get("description", "")
        return html_to_text(description) if description else None
    except Exception:
        return None


def fetch_html(job: dict, client: httpx.Client) -> str | None:
    url = job.get("url", "")
    if not url:
        return None
    try:
        r = client.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        r.raise_for_status()
        return html_to_text(r.text) or None
    except Exception:
        return None


def fetch_workday(job: dict, client: httpx.Client) -> str | None:
    """Workday detail: insert /wday/cxs/{tenant}/ between the host and the board path."""
    slug = job.get("company_slug", "")
    url = job.get("url", "")
    if not slug or not url:
        return None
    try:
        tenant, partition, board = slug.split("/", 2)
    except ValueError:
        return None
    host_prefix = f"https://{tenant}.{partition}.myworkdayjobs.com/{board}"
    if not url.startswith(host_prefix):
        return None
    external_path = url[len(host_prefix):]
    detail_url = f"https://{tenant}.{partition}.myworkdayjobs.com/wday/cxs/{tenant}/{board}{external_path}"
    try:
        r = client.get(
            detail_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        info = r.json().get("jobPostingInfo", {})
        description = info.get("jobDescription") or ""
        return html_to_text(description) if description else None
    except Exception:
        return None


def fetch_eightfold(job: dict, client: httpx.Client) -> str | None:
    """Eightfold position detail via pcsx API."""
    slug = job.get("company_slug", "")
    if not slug or "|" not in slug:
        return None
    host, _ = slug.split("|", 1)
    # Job id format: eightfold-{domain-with-dashes}-{position_id}
    job_id = job.get("id", "")
    pos_id = job_id.rsplit("-", 1)[-1] if "-" in job_id else job_id
    if not pos_id:
        return None
    detail_url = f"https://{host}/api/pcsx/position/{pos_id}"
    try:
        r = client.get(
            detail_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": f"https://{host}/careers",
            },
            timeout=15,
        )
        r.raise_for_status()
        position = r.json().get("data", {}).get("position", {})
        description = (
            position.get("description")
            or position.get("jobDescription")
            or position.get("body")
            or ""
        )
        return html_to_text(description) if description else None
    except Exception:
        return None


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "bamboo": fetch_bamboo,
    "breezy": fetch_html,
    "workable": fetch_html,
    "workday": fetch_workday,
    "eightfold": fetch_eightfold,
}


def main():
    fetch_all = "--all" in sys.argv
    today = datetime.now(timezone.utc).date().isoformat()

    jobs = json.loads(JOBS_FILE.read_text())

    to_fetch = [
        j for j in jobs
        if j.get("source") in FETCHERS
        and not j.get("raw_text", "").strip()
        and (fetch_all or j.get("first_seen") == today)
    ]

    if not to_fetch:
        scope = "all" if fetch_all else "today's"
        print(f"No {scope} jobs need descriptions")
        return

    scope = "all" if fetch_all else "today's new"
    print(f"Fetching descriptions for {len(to_fetch)} {scope} jobs...")

    job_index = {j["id"]: j for j in jobs}
    lock = threading.Lock()
    fetched = failed = 0

    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(FETCHERS[job["source"]], job, client): job
                for job in to_fetch
            }
            for future in as_completed(futures):
                job = futures[future]
                desc = future.result()
                with lock:
                    if desc:
                        job_index[job["id"]]["raw_text"] = desc
                        fetched += 1
                    else:
                        failed += 1

    JOBS_FILE.write_text(json.dumps(list(job_index.values()), indent=2))
    print(f"Fetched: {fetched}, Failed: {failed}")


if __name__ == "__main__":
    main()
