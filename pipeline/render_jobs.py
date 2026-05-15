#!/usr/bin/env python3
"""Render classified engineering jobs to one markdown file per job under jobs/."""

import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path


JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
CLASSIFIED_FILE = Path(__file__).parent.parent / "data" / "jobs_classified.json"
COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies_classified.json"
COMPANIES_DOMAINS_FILE = Path(__file__).parent.parent / "data" / "companies.json"

HASH_MARKER = "render_hash: "
FORMAT_VERSION = "16"  # bump to force re-render of all files
SKILL_COLOR = "3B82F6"
REMOTE_BADGE = '<img src="https://img.shields.io/badge/Remote-22C55E?style=flat-square" align="absmiddle">'
HYBRID_BADGE = '<img src="https://img.shields.io/badge/Hybrid-F59E0B?style=flat-square" align="absmiddle">'


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


def skill_badge(skill: str) -> str:
    label = skill.strip().replace("-", "--").replace("_", "__").replace(" ", "_")
    label = (label
        .replace("(", "%28").replace(")", "%29")
        .replace(",", "%2C").replace("/", "%2F")
        .replace("+", "%2B").replace("#", "%23"))
    return f"![{skill}](https://img.shields.io/badge/{label}-{SKILL_COLOR}?style=flat-square)"


def render_hash(job: dict, classification: dict) -> str:
    skills_str = ",".join(classification.get("skills") or [])
    level = classification.get("level") or ""
    comp = classification.get("comp") or ""
    location = classification.get("location") or ""
    region = classification.get("region") or ""
    key = f"v{FORMAT_VERSION}:{job['id']}:{job['title']}:{job.get('raw_text', '')[:200]}:{classification.get('job_summary', '')}:{skills_str}:{level}:{comp}:{location}:{region}"
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
    lines = [l.rstrip() for l in text.split("\n")]
    paragraphs = []
    for line in lines:
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


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
        display_location = clean_location(display_location, True)  # also strip "hybrid" mentions

    # HTML comment holds machine-readable metadata — not rendered by GitHub
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

    detail_parts = []
    if display_location and display_location != "Not specified":
        detail_parts.append(display_location)
    if level:
        detail_parts.append(f"`{level.capitalize()}`")
    if remote_str == "Remote":
        detail_parts.append(REMOTE_BADGE)
    elif is_hybrid:
        detail_parts.append(HYBRID_BADGE)
    elif remote_str == "On-site":
        detail_parts.append("On-site")
    if comp:
        detail_parts.append(f"`{comp}`")
    for extra in comp_extras:
        detail_parts.append(f"`{extra.capitalize()}`")

    logo = f'<a href="https://{domain}"><img src="https://www.google.com/s2/favicons?domain={domain}&sz=32" width="16" height="16" align="absmiddle"></a>&ensp;' if domain else ""
    company_line = f"{logo}**{job['company']}**"
    meta_line = (company_line + " · " + " · ".join(detail_parts)) if detail_parts else company_line

    lines = meta_lines + [
        "",
        f"# {job['title']}",
        "",
        meta_line,
        "",
    ]

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

    written_paths: set[Path] = set()
    written = skipped = 0

    for job in eng_jobs:
        cl = classified[job["id"]]
        company_slug = job.get("company_slug", "")
        company_summary = company_summaries.get(company_slug)

        company_dir = JOBS_DIR / slugify(job["company"])
        company_dir.mkdir(exist_ok=True)

        path = company_dir / f"{title_slug(job['title'])}-{native_id(job['id'])}.md"
        written_paths.add(path)

        if read_hash(path) == render_hash(job, cl):
            skipped += 1
            continue

        domain = company_domains.get(job["company"], "")
        path.write_text(render_job(job, cl, company_summary, domain))
        written += 1

    removed = 0
    for stale_path in JOBS_DIR.rglob("*.md"):
        if stale_path not in written_paths:
            stale_path.unlink()
            removed += 1
            if not any(stale_path.parent.iterdir()):
                stale_path.parent.rmdir()

    print(f"Written: {written}, Skipped (unchanged): {skipped}, Removed (stale): {removed}")


if __name__ == "__main__":
    main()
