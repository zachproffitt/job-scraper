from datetime import datetime, timezone

import httpx

from ._base import Job, ScraperError

# Slug format: "host|domain" e.g. "apply.careers.microsoft.com|microsoft.com"
# host = the Eightfold-powered careers URL host
# domain = the domain parameter passed to the search API
LIST_URL = "https://{host}/api/pcsx/search"
PAGE_SIZE = 20


def scrape(company: str, slug: str) -> list[Job]:
    try:
        host, domain = slug.split("|", 1)
    except ValueError:
        raise ScraperError(f"Eightfold slug must be host|domain, got: {slug!r}")

    url = LIST_URL.format(host=host)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Referer": f"https://{host}/careers",
    }

    all_positions = []
    start = 0

    try:
        while True:
            r = httpx.get(url, params={"domain": domain, "start": str(start), "num": str(PAGE_SIZE)},
                          headers=headers, timeout=20)
            r.raise_for_status()
            try:
                data = r.json()
            except Exception as e:
                raise ScraperError(f"Eightfold JSON parse failed for {slug}: {e}") from e

            positions = data.get("data", {}).get("positions", [])
            if not positions:
                break
            all_positions.extend(positions)
            total = data.get("data", {}).get("count", 0)
            if len(all_positions) >= total:
                break
            start += PAGE_SIZE
    except httpx.HTTPError as e:
        raise ScraperError(f"Eightfold request failed for {slug}: {e}") from e

    jobs = []
    for item in all_positions:
        pos_id = str(item.get("id", ""))
        pos_url = item.get("positionUrl", "")
        job_url = f"https://{host}{pos_url}" if pos_url else f"https://{host}/careers"

        locations = item.get("locations") or []
        location = locations[0] if locations else None

        work_option = (item.get("workLocationOption") or "").lower()
        remote = work_option in ("remote", "work from home")

        posted_ts = item.get("postedTs")
        try:
            posted_at = datetime.fromtimestamp(posted_ts, tz=timezone.utc).date() if posted_ts else None
        except (OSError, ValueError, TypeError):
            posted_at = None

        jobs.append(Job(
            id=f"eightfold-{domain.replace('.', '-')}-{pos_id}",
            company=company,
            company_slug=slug,
            title=item.get("name", ""),
            url=job_url,
            source="eightfold",
            location=location,
            remote=remote,
            posted_at=posted_at,
            raw_text="",
        ))

    return jobs
