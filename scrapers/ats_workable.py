from datetime import datetime

import httpx

from ._base import Job, ScraperError

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
        pub = item.get("published", "")
        try:
            posted_at = datetime.fromisoformat(pub.replace("Z", "+00:00")).date() if pub else None
        except (ValueError, AttributeError):
            posted_at = None

        loc = item.get("location", {})
        city = loc.get("city") or ""
        country = loc.get("country") or ""
        location = ", ".join(filter(None, [city, country])) or None
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
