from datetime import date

import httpx

from ._base import Job, ScraperError, html_to_text

API_URL = "https://{tenant}.{partition}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
JOB_URL = "https://{tenant}.{partition}.myworkdayjobs.com/{board}{path}"


def scrape(company: str, slug: str) -> list[Job]:
    # slug format: tenant/partition/board (e.g. crowdstrike/wd5/crowdstrikecareers)
    try:
        tenant, partition, board = slug.split("/", 2)
    except ValueError:
        raise ScraperError(f"Workday slug must be tenant/partition/board, got: {slug!r}")

    url = API_URL.format(tenant=tenant, partition=partition, board=board)
    offset = 0
    limit = 20
    total = None  # only populated on first response
    all_postings = []

    try:
        while True:
            r = httpx.post(
                url,
                json={"limit": limit, "offset": offset, "searchText": "", "appliedFacets": {}},
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break
            all_postings.extend(postings)
            if total is None:
                total = data.get("total", 0)
            if len(all_postings) >= total:
                break
            offset += limit
    except httpx.HTTPError as e:
        raise ScraperError(f"Workday request failed for {slug}: {e}") from e
    except Exception as e:
        raise ScraperError(f"Workday unexpected error for {slug}: {e}") from e

    jobs = []
    for item in all_postings:
        external_path = item.get("externalPath", "")
        job_url = JOB_URL.format(tenant=tenant, partition=partition, board=board, path=external_path)
        # Extract a native ID from the path (last path segment)
        native_id = external_path.rsplit("/", 1)[-1] if "/" in external_path else external_path

        location = item.get("locationsText") or None
        remote = "remote" in (location or "").lower()

        jobs.append(Job(
            id=f"workday-{tenant}-{native_id}",
            company=company,
            company_slug=slug,
            title=item.get("title") or item.get("jobTitle", ""),
            url=job_url,
            source="workday",
            location=location,
            remote=remote,
            posted_at=None,
            raw_text="",
        ))

    return jobs
