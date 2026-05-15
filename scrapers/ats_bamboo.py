from datetime import date

import httpx

from ._base import Job, ScraperError

LIST_URL = "https://{slug}.bamboohr.com/careers/list"

# locationType: "0" = on-site, "2" = remote; anything else is unclear
_LOCATION_TYPE_REMOTE = {"2": True, "0": False}


def scrape(company: str, slug: str) -> list[Job]:
    try:
        r = httpx.get(
            LIST_URL.format(slug=slug),
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScraperError(f"BambooHR request failed for {slug}: {e}") from e

    try:
        data = r.json()
    except (ValueError, httpx.DecodingError) as e:
        raise ScraperError(f"BambooHR JSON parse failed for {slug}: {e}") from e

    jobs = []
    for item in data.get("result", []):
        job_id = str(item["id"])
        loc = item.get("location", {})
        city = loc.get("city") or ""
        state = loc.get("state") or ""
        location = ", ".join(filter(None, [city, state])) or None
        loc_type = str(item.get("locationType", ""))
        remote = _LOCATION_TYPE_REMOTE.get(loc_type)

        jobs.append(Job(
            id=f"bamboo-{slug}-{job_id}",
            company=company,
            company_slug=slug,
            title=item["jobOpeningName"],
            url=f"https://{slug}.bamboohr.com/careers/{job_id}",
            source="bamboo",
            location=location,
            remote=remote,
            posted_at=None,
            raw_text="",
        ))

    return jobs
