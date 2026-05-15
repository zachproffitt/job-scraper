import httpx

from ._base import Job, ScraperError, build_location, parse_iso_date

LIST_URL = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"


def scrape(company: str, slug: str) -> list[Job]:
    try:
        r = httpx.post(
            LIST_URL.format(slug=slug),
            json={"query": "", "location": [], "department": [], "worktype": [], "remote": []},
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScraperError(f"Workable request failed for {slug}: {e}") from e

    try:
        data = r.json()
    except (ValueError, httpx.DecodingError) as e:
        raise ScraperError(f"Workable JSON parse failed for {slug}: {e}") from e

    jobs = []
    for item in data.get("results", []):
        posted_at = parse_iso_date(item.get("published"))

        loc = item.get("location", {})
        location = build_location(loc.get("city"), loc.get("country"))
        shortcode = item.get("shortcode", "")

        jobs.append(Job(
            id=f"workable-{slug}-{shortcode}",
            company=company,
            company_slug=slug,
            title=item["title"],
            url=f"https://apply.workable.com/{slug}/j/{shortcode}/",
            source="workable",
            location=location,
            remote=bool(item.get("remote")),
            posted_at=posted_at,
            raw_text="",
        ))

    return jobs
