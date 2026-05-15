from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from ._base import Job, ScraperError, build_location, html_to_text, parse_iso_date

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
WORKERS = 8


def fetch_description(slug: str, job_id: str) -> str | None:
    try:
        r = httpx.get(DETAIL_URL.format(slug=slug, job_id=job_id), timeout=15)
        r.raise_for_status()
        sections = r.json().get("jobAd", {}).get("sections", {})
        text = sections.get("jobDescription", {}).get("text", "")
        return html_to_text(text) if text else None
    except Exception:
        return None


def scrape(company: str, slug: str) -> list[Job]:
    try:
        r = httpx.get(LIST_URL.format(slug=slug), timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScraperError(f"SmartRecruiters request failed for {slug}: {e}") from e

    items = r.json().get("content", [])
    if not items:
        return []

    # Fetch all descriptions concurrently
    descriptions: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(fetch_description, slug, item["id"]): item["id"]
            for item in items
        }
        for future in as_completed(futures):
            job_id = futures[future]
            descriptions[job_id] = future.result()

    jobs = []
    for item in items:
        loc = item.get("location", {})
        remote = loc.get("remote")
        location = build_location(loc.get("city"), loc.get("country"))

        jobs.append(Job(
            id=f"smartrecruiters-{slug}-{item['id']}",
            company=company,
            company_slug=slug,
            title=item["name"],
            url=item.get("ref", f"https://jobs.smartrecruiters.com/{slug}/{item['id']}"),
            source="smartrecruiters",
            location=location,
            remote=remote if isinstance(remote, bool) else None,
            posted_at=parse_iso_date(item.get("releasedDate")),
            raw_text=descriptions.get(item["id"]),
        ))

    return jobs
