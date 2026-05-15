import httpx

from ._base import Job, ScraperError, parse_iso_date

LIST_URL = "https://{slug}.breezy.hr/json"


def scrape(company: str, slug: str) -> list[Job]:
    try:
        r = httpx.get(LIST_URL.format(slug=slug), follow_redirects=True, timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ScraperError(f"Breezy request failed for {slug}: {e}") from e

    try:
        data = r.json()
    except (ValueError, httpx.DecodingError) as e:
        raise ScraperError(f"Breezy JSON parse failed for {slug}: {e}") from e

    if not isinstance(data, list):
        raise ScraperError(f"Breezy unexpected response shape for {slug}: {type(data).__name__}")

    jobs = []
    for item in data:
        posted_at = parse_iso_date(item.get("published_date"))

        locations = item.get("locations") or [item.get("location", {})]
        primary = next((l for l in locations if l.get("primary")), locations[0] if locations else {})
        is_remote = bool(primary.get("is_remote"))
        location = primary.get("name") or None

        jobs.append(Job(
            id=f"breezy-{slug}-{item['id']}",
            company=company,
            company_slug=slug,
            title=item["name"],
            url=item["url"],
            source="breezy",
            location=location,
            remote=is_remote,
            posted_at=posted_at,
            raw_text="",
        ))

    return jobs
