#!/usr/bin/env python3
"""Render classified engineering jobs to one markdown file per job under jobs/."""

import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path


JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
CLASSIFIED_FILE = Path(__file__).parent.parent / "data" / "jobs_classified.json"
COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies_classified.json"
COMPANIES_DOMAINS_FILE = Path(__file__).parent.parent / "data" / "companies.json"

from badges import REMOTE_BADGE, HYBRID_BADGE, skill_badge

HASH_MARKER = "render_hash: "
FORMAT_VERSION = "16"  # bump to force re-render of all files

# US state codes + common country codes used by ATSs in job titles
_LOCATION_CODES = frozenset({
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
    "USA","UK","GB","DE","FR","AU","SG","NL","SE","CH",
    "ES","PL","JP","KR","BR","MX","IE","HK","AE","IN",
})


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")


def title_slug(title: str) -> str:
    t = re.sub(r"\bC\+\+", "cpp", title, flags=re.I)
    t = re.sub(r"\bC#", "csharp", t, flags=re.I)
    t = re.sub(r"\bF#", "fsharp", t, flags=re.I)
    t = re.sub(r"\bQ#", "qsharp", t, flags=re.I)
    t = re.sub(r"\.NET\b", "dotnet", t, flags=re.I)
    t = re.sub(r"[^a-z0-9]+", "-", t.lower())
    return t.strip("-")


def native_id(job_id: str) -> str:
    """Strip '{ats}-{company}-' prefix, keeping the full ATS-native ID."""
    parts = job_id.split("-", 2)
    return parts[2] if len(parts) >= 3 else job_id


def strip_location_from_title(title: str) -> str:
    """Remove ATS-appended location suffix for grouping (e.g. ' - Birmingham, AL, USA')."""
    m = re.search(
        r'\s*[-–]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*,\s+([A-Z]{2,3})(?:,\s+[A-Z]{2,3})?\s*$',
        title,
    )
    if m and m.group(1) in _LOCATION_CODES:
        return title[:m.start()].strip()
    return title


def render_hash(job: dict, classification: dict) -> str:
    skills_str = ",".join(classification.get("skills") or [])
    level = classification.get("level") or ""
    comp = classification.get("comp") or ""
    location = classification.get("location") or ""
    region = classification.get("region") or ""
    key = f"v{FORMAT_VERSION}:{job['id']}:{job['title']}:{job.get('raw_text', '')[:200]}:{classification.get('job_summary', '')}:{skills_str}:{level}:{comp}:{location}:{region}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


def group_render_hash(base_title: str, jobs: list[dict], first_cl: dict) -> str:
    ids_str = ",".join(sorted(j["id"] for j in jobs))
    locs_str = ",".join(sorted(
        (first_cl.get("location") or j.get("location") or "") for j in jobs
    ))
    skills_str = ",".join(first_cl.get("skills") or [])
    level = first_cl.get("level") or ""
    comp = first_cl.get("comp") or ""
    key = f"v{FORMAT_VERSION}:group:{ids_str}:{base_title}:{jobs[0].get('raw_text', '')[:200]}:{first_cl.get('job_summary', '')}:{skills_str}:{level}:{comp}:{locs_str}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


def clean_location(location: str, is_remote: bool) -> str:
    """Strip 'remote'/'hybrid' from location string when the tag is already shown."""
    if not is_remote or not location:
        return location
    cleaned = re.sub(r"\bremote[\s-]friendly\b", "", location, flags=re.I)
    cleaned = re.sub(r"\s*\(\s*(?:remote|hybrid)\s*\)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*[-–,|]\s*(?:remote|hybrid)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:remote|hybrid)\s*[-–,|]\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*(?:remote|hybrid)\s*$", "", cleaned, flags=re.I)
    return cleaned.strip().strip("-").strip(",").strip("|").strip()


def format_date(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d")
    except ValueError:
        return iso


def pretty_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        return iso


def pretty_first_seen(date_iso: str, ts_iso: str | None) -> str:
    label = pretty_date(date_iso)
    if ts_iso:
        try:
            dt = datetime.fromisoformat(ts_iso)
            label += f" at {dt.strftime('%H:%M')} UTC"
        except ValueError:
            pass
    return label


def format_description(text: str) -> str:
    """Convert single newlines to paragraph breaks so markdown renders correctly."""
    return "\n\n".join(line.rstrip() for line in text.split("\n") if line.rstrip())


def _build_detail_parts(location: str, level: str | None, remote_str: str,
                        is_hybrid: bool, comp: str | None, comp_extras: list[str]) -> list[str]:
    parts = []
    if location and location != "Not specified":
        parts.append(location)
    if level:
        parts.append(f"`{level.capitalize()}`")
    if remote_str == "Remote":
        parts.append(REMOTE_BADGE)
    elif is_hybrid:
        parts.append(HYBRID_BADGE)
    elif remote_str == "On-site":
        parts.append("On-site")
    if comp:
        parts.append(f"`{comp}`")
    for extra in comp_extras:
        parts.append(f"`{extra.capitalize()}`")
    return parts


def _company_logo_html(domain: str) -> str:
    if not domain:
        return ""
    return (
        f'<a href="https://{domain}">'
        f'<img src="https://www.google.com/s2/favicons?domain={domain}&sz=32"'
        f' width="16" height="16" align="absmiddle"></a>&ensp;'
    )


def render_job(job: dict, classification: dict, company_summary: str | None, domain: str = "") -> str:
    raw_location = job.get("location") or ""
    location = classification.get("location") or raw_location or "Not specified"
    remote_str = {True: "Remote", False: "On-site"}.get(job.get("remote"), "Not specified")

    posted = format_date(job.get("posted_at"))
    first_seen = job.get("first_seen") or datetime.now(timezone.utc).date().isoformat()
    first_seen_at = job.get("first_seen_at")
    raw_text = (job.get("raw_text") or "").strip()
    job_summary = classification.get("job_summary") or ""
    skills = classification.get("skills") or []
    level = classification.get("level")
    is_hybrid = classification.get("is_hybrid", False)
    comp = classification.get("comp")
    comp_extras = classification.get("comp_extras") or []
    rhash = render_hash(job, classification)

    is_remote = job.get("remote") is True
    display_location = clean_location(location, is_remote)
    if is_hybrid:
        display_location = clean_location(display_location, True)

    meta_lines = [
        "<!--",
        f"id: {job['id']}",
        f"company: {job['company']}",
        f"title: {job['title']}",
        f"source: {job['source']}",
        f"location: {location}",
        f"remote: {remote_str}",
        f"hybrid: {'yes' if is_hybrid else 'no'}",
        f"posted_at: {posted or 'Unknown'}",
        f"first_seen: {first_seen}",
        f"first_seen_at: {first_seen_at or ''}",
        f"url: {job['url']}",
        f"summary: {job_summary}",
        f"skills: {', '.join(skills)}",
        f"level: {level or ''}",
        f"comp: {comp or ''}",
        f"comp_extras: {', '.join(comp_extras)}",
        f"render_hash: {rhash}",
        "-->",
    ]

    detail_parts = _build_detail_parts(display_location, level, remote_str, is_hybrid, comp, comp_extras)
    logo = _company_logo_html(domain)
    company_line = f"{logo}**{job['company']}**"
    meta_line = (company_line + " · " + " · ".join(detail_parts)) if detail_parts else company_line

    lines = meta_lines + ["", f"# {job['title']}", "", meta_line, ""]

    if company_summary:
        lines += [f"> {company_summary}", ""]
    if job_summary:
        lines += [f"_{job_summary}_", ""]
    if skills:
        lines += [" ".join(skill_badge(s) for s in skills), ""]

    date_label = f"Posted {pretty_date(posted)}" if posted else f"First seen {pretty_first_seen(first_seen, first_seen_at)}"
    lines += [f"<sub>{date_label}</sub>", ""]
    lines += [f"**[→ Apply]({job['url']})**", ""]

    if raw_text:
        lines += ["---", "", format_description(raw_text), "", "---", "", f"**[→ Apply]({job['url']})**", ""]

    return "\n".join(lines)


def render_job_group(base_title: str, jobs: list[dict], classified: dict[str, dict],
                     company_summary: str | None, domain: str = "") -> str:
    """Render one consolidated page for the same role posted across multiple cities."""
    # Use first job's classification for shared fields
    first_job = jobs[0]
    first_cl = classified[first_job["id"]]

    remote_str = {True: "Remote", False: "On-site"}.get(first_job.get("remote"), "Not specified")
    posted = format_date(first_job.get("posted_at"))
    job_summary = first_cl.get("job_summary") or ""
    skills = first_cl.get("skills") or []
    level = first_cl.get("level")
    is_hybrid = first_cl.get("is_hybrid", False)
    comp = first_cl.get("comp")
    comp_extras = first_cl.get("comp_extras") or []

    # Collect per-city location + URL, deduped
    city_entries: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for job in jobs:
        cl = classified[job["id"]]
        loc = cl.get("location") or job.get("location") or ""
        url = job["url"]
        if url not in seen_urls:
            city_entries.append((loc, url))
            seen_urls.add(url)

    combined_location = " / ".join(loc for loc, _ in city_entries if loc) or "Multiple locations"

    # Earliest first_seen across the group
    first_seen = min(
        (j.get("first_seen") or datetime.now(timezone.utc).date().isoformat() for j in jobs)
    )
    first_seen_at = min(
        (j.get("first_seen_at") or "" for j in jobs),
        default="",
    ) or None

    rhash = group_render_hash(base_title, jobs, first_cl)

    meta_lines = [
        "<!--",
        f"id: {first_job['id']}",
        f"company: {first_job['company']}",
        f"title: {base_title}",
        f"source: {first_job['source']}",
        f"location: {combined_location}",
        f"remote: {remote_str}",
        f"hybrid: {'yes' if is_hybrid else 'no'}",
        f"posted_at: {posted or 'Unknown'}",
        f"first_seen: {first_seen}",
        f"first_seen_at: {first_seen_at or ''}",
        f"url: ",
        f"summary: {job_summary}",
        f"skills: {', '.join(skills)}",
        f"level: {level or ''}",
        f"comp: {comp or ''}",
        f"comp_extras: {', '.join(comp_extras)}",
        f"render_hash: {rhash}",
        "-->",
    ]

    display_location = clean_location(combined_location, remote_str == "Remote")
    if is_hybrid:
        display_location = clean_location(display_location, True)

    detail_parts = _build_detail_parts(display_location, level, remote_str, is_hybrid, comp, comp_extras)
    logo = _company_logo_html(domain)
    company_line = f"{logo}**{first_job['company']}**"
    meta_line = (company_line + " · " + " · ".join(detail_parts)) if detail_parts else company_line

    apply_links = "\n".join(
        f"- **[{loc or 'Apply'} →]({url})**" for loc, url in city_entries
    )

    lines = meta_lines + ["", f"# {base_title}", "", meta_line, ""]

    if company_summary:
        lines += [f"> {company_summary}", ""]
    if job_summary:
        lines += [f"_{job_summary}_", ""]
    if skills:
        lines += [" ".join(skill_badge(s) for s in skills), ""]

    date_label = f"Posted {pretty_date(posted)}" if posted else f"First seen {pretty_first_seen(first_seen, first_seen_at)}"
    lines += [f"<sub>{date_label}</sub>", ""]

    lines += ["**Apply by location:**", apply_links, ""]

    # Description from first job that has one
    raw_text = next((j.get("raw_text", "").strip() for j in jobs if j.get("raw_text", "").strip()), "")
    if raw_text:
        lines += ["---", "", format_description(raw_text), "", "---", ""]

    lines += ["**Apply by location:**", apply_links, ""]

    return "\n".join(lines)


def read_hash(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith("render_hash:"):
                return line.removeprefix("render_hash:").strip()
    except FileNotFoundError:
        pass
    return None


def main():
    JOBS_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent.parent / "jobs" / "jobs"

    jobs = json.loads(JOBS_FILE.read_text())

    classified: dict[str, dict] = {}
    if CLASSIFIED_FILE.exists():
        classified = json.loads(CLASSIFIED_FILE.read_text())

    company_summaries: dict[str, str] = {}
    if COMPANIES_FILE.exists():
        for c in json.loads(COMPANIES_FILE.read_text()):
            company_summaries[c["slug"]] = c.get("summary", "")

    company_domains: dict[str, str] = {}
    if COMPANIES_DOMAINS_FILE.exists():
        for c in json.loads(COMPANIES_DOMAINS_FILE.read_text()):
            if c.get("website") and c.get("name"):
                domain = c["website"].removeprefix("https://").removeprefix("http://").split("/")[0]
                company_domains[c["name"]] = domain

    JOBS_DIR.mkdir(exist_ok=True)

    eng_jobs = [
        j for j in jobs
        if classified.get(j["id"], {}).get("is_engineering") is True
        and not classified.get(j["id"], {}).get("is_contract", False)
        and classified.get(j["id"], {}).get("region", "unclear") in ("us", "canada", "unclear")
    ]

    print(f"Engineering jobs to render: {len(eng_jobs)} / {len(jobs)} total")

    # Group by (company, base_title) to consolidate multi-city postings
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for job in eng_jobs:
        base_title = strip_location_from_title(job["title"])
        groups[(job["company"], base_title)].append(job)

    written_paths: set[Path] = set()
    written = skipped = 0

    for (company, base_title), group_jobs in groups.items():
        first_job = group_jobs[0]
        company_slug = first_job.get("company_slug", "")
        company_summary = company_summaries.get(company_slug)
        domain = company_domains.get(company, "")

        company_dir = JOBS_DIR / slugify(company)
        company_dir.mkdir(exist_ok=True)

        if len(group_jobs) == 1:
            job = group_jobs[0]
            cl = classified[job["id"]]
            path = company_dir / f"{title_slug(job['title'])}-{native_id(job['id'])}.md"
            written_paths.add(path)

            if read_hash(path) == render_hash(job, cl):
                skipped += 1
                continue

            path.write_text(render_job(job, cl, company_summary, domain))
            written += 1
        else:
            first_cl = classified[first_job["id"]]
            path = company_dir / f"{title_slug(base_title)}.md"
            written_paths.add(path)

            if read_hash(path) == group_render_hash(base_title, group_jobs, first_cl):
                skipped += 1
                continue

            path.write_text(render_job_group(base_title, group_jobs, classified, company_summary, domain))
            written += 1

    removed = 0
    for stale_path in JOBS_DIR.rglob("*.md"):
        if stale_path not in written_paths:
            stale_path.unlink()
            removed += 1
            if not any(stale_path.parent.iterdir()):
                stale_path.parent.rmdir()

    print(f"Written: {written}, Skipped (unchanged): {skipped}, Removed (stale): {removed}")
    multi = sum(1 for jobs in groups.values() if len(jobs) > 1)
    if multi:
        print(f"Consolidated {multi} multi-city groups")


if __name__ == "__main__":
    main()
