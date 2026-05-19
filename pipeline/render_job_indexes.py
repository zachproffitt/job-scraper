#!/usr/bin/env python3
"""Generate README.md, REMOTE.md, and COMPANIES.md for the jobs repo."""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from badges import REMOTE_BADGE, HYBRID_BADGE, NEW_BADGE, skill_badge
from render_common import (
    clean_location, company_logo_html, abbrev_comp,
    strip_location_from_title, is_new_within, SUPPORTED_ATS,
)


DATA_DIR = Path(__file__).parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs_raw.json"
CLASSIFIED_FILE = DATA_DIR / "jobs_classified.json"
COMPANIES_FILE = DATA_DIR / "companies.json"
COMPANIES_CLASSIFIED_FILE = DATA_DIR / "companies_classified.json"

JOBS_REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent.parent / "jobs"
README = JOBS_REPO / "README.md"
REMOTE_README = JOBS_REPO / "REMOTE.md"
COMPANIES_README = JOBS_REPO / "COMPANIES.md"


def collect_jobs() -> tuple[list[dict], dict[str, str]]:
    """Read from data files, filter to renderable engineering jobs, deduplicate multi-city roles."""
    company_logos: dict[str, str] = {}
    if COMPANIES_FILE.exists():
        for c in json.loads(COMPANIES_FILE.read_text()):
            if c.get("website") and c.get("name"):
                domain = c["website"].removeprefix("https://").removeprefix("http://").split("/")[0]
                company_logos[c["name"]] = domain

    raw_jobs = json.loads(JOBS_FILE.read_text()) if JOBS_FILE.exists() else []
    classified: dict[str, dict] = {}
    if CLASSIFIED_FILE.exists():
        classified = json.loads(CLASSIFIED_FILE.read_text())

    eng_jobs = [
        j for j in raw_jobs
        if classified.get(j["id"], {}).get("is_engineering") is True
        and not classified.get(j["id"], {}).get("is_contract", False)
        and classified.get(j["id"], {}).get("region") in ("us", "canada", "unclear")
    ]

    # Deduplicate multi-city roles — same company + normalized title → one index entry
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for job in eng_jobs:
        base_title = strip_location_from_title(job["title"])
        groups[(job["company"], base_title)].append(job)

    result = []
    for (company, base_title), group_jobs in groups.items():
        rep = group_jobs[0]
        cl = classified[rep["id"]]

        raw_location = cl.get("location") or rep.get("location") or ""
        remote_bool = rep.get("remote")
        remote_str = {True: "Remote", False: "On-site"}.get(remote_bool, "Not specified")
        is_hybrid = cl.get("is_hybrid", False)

        result.append({
            "title": base_title,
            "company": company,
            "url": rep["url"],
            "summary": cl.get("job_summary") or "",
            "skills": cl.get("skills") or [],
            "first_seen": min(j.get("first_seen") or "" for j in group_jobs),
            "first_seen_at": min((j.get("first_seen_at") or "" for j in group_jobs), default="") or "",
            "posted_at": rep.get("posted_at") or "",
            "remote": remote_bool is True,
            "remote_str": remote_str,
            "hybrid": "yes" if is_hybrid else "",
            "location_raw": raw_location,
            "region": cl.get("region") or "unclear",
            "level": cl.get("level") or "",
            "comp": cl.get("comp") or "",
            "comp_extras": cl.get("comp_extras") or [],
        })

    return result, company_logos


def format_meta(j: dict) -> str:
    location = j["location_raw"]
    if " | " in location:
        location = location.split(" | ")[0].strip()
    if location in ("Not specified", ""):
        location = ""

    is_remote = j["remote_str"] == "Remote" or location.lower() == "remote"
    location = clean_location(location, is_remote=is_remote, expand_state=j.get("region") == "us")

    parts = [f"**{j['company']}**"]
    if location and location.lower() != "remote":
        parts.append(location)
    if is_remote:
        parts.append(REMOTE_BADGE)
    elif j["hybrid"] == "yes":
        parts.append(HYBRID_BADGE)
    if j["level"] and j["level"] not in ("unclear", ""):
        parts.append(f"`{j['level'].capitalize()}`")
    if j["comp"]:
        parts.append(f"`{abbrev_comp(j['comp'])}`")
    for extra in j["comp_extras"]:
        parts.append(f"`{extra.capitalize()}`")

    return " · ".join(parts)


def format_job_meta(j: dict) -> str:
    parts = []
    location = j["location_raw"]
    if " | " in location:
        location = location.split(" | ")[0].strip()
    is_remote = j["remote_str"] == "Remote" or location.lower() == "remote"
    is_hybrid = j["hybrid"] == "yes"
    if location and location != "Not specified":
        location = clean_location(location, is_remote=is_remote, expand_state=j.get("region") == "us")
        if location and location.lower() != "remote":
            parts.append(location)
    if is_remote:
        parts.append(REMOTE_BADGE)
    elif is_hybrid:
        parts.append(HYBRID_BADGE)
    if j["level"] and j["level"] not in ("unclear", ""):
        parts.append(f"`{j['level'].capitalize()}`")
    if j["comp"]:
        parts.append(f"`{abbrev_comp(j['comp'])}`")
    for extra in j["comp_extras"]:
        parts.append(f"`{extra.capitalize()}`")
    return " · ".join(parts)


def render_index(jobs: list[dict], company_logos: dict[str, str], company_count: int,
                 out_path: Path, title: str, subtitle: str, remote_only: bool = False) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    if remote_only:
        jobs = [j for j in jobs if j["remote"]]

    by_date: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        by_date[j["first_seen"]].append(j)

    total = sum(len(v) for v in by_date.values())
    new_recent = sum(1 for j in jobs if is_new_within(j, cutoff))

    stats = f"**{total} open roles** ({new_recent} new)"
    if not remote_only:
        stats += f" &nbsp;·&nbsp; {company_count} companies searched"

    if remote_only:
        nav_links = "[← All roles](README.md) &nbsp;·&nbsp; [By company →](COMPANIES.md) &nbsp;·&nbsp; [How it works →](https://github.com/zachproffitt/builder-jobs-scraper)"
    else:
        nav_links = "[By company →](COMPANIES.md) &nbsp;·&nbsp; [Remote only →](REMOTE.md) &nbsp;·&nbsp; [How it works →](https://github.com/zachproffitt/builder-jobs-scraper)"

    lines = [
        f"# {title}",
        "",
        subtitle,
        "",
        f"### {stats}",
        "",
        nav_links,
        "",
    ]

    for dt in sorted(by_date.keys(), reverse=True):
        date_jobs = by_date[dt]
        date_jobs.sort(key=lambda j: (j["first_seen_at"] or ""), reverse=True)
        try:
            label = datetime.strptime(dt, "%Y-%m-%d").strftime("%B %-d, %Y")
        except ValueError:
            label = dt
        lines.append("<br>")
        lines.append("")
        lines.append(f"## {label}")
        lines.append("")
        for j in date_jobs:
            lines.append(f"### [{j['title']}]({j['url']})")
            domain = company_logos.get(j["company"], "")
            logo = company_logo_html(domain)
            meta = format_meta(j)
            lines.append(f"{logo}{meta}")
            if j["summary"]:
                lines.append("")
                apply = f" · [Apply →]({j['url']})" if j.get("url") else ""
                lines.append(f"_{j['summary']}{apply}_")
            if j["skills"]:
                lines.append("")
                lines.append(" ".join(skill_badge(s) for s in j["skills"]))
            lines.append("")
            ts = j.get("first_seen_at", "")
            if ts:
                try:
                    dt_obj = datetime.fromisoformat(ts)
                    lines.append(f"<sub>{dt_obj.strftime('%B %-d, %Y at %H:%M UTC')}</sub>")
                except ValueError:
                    lines.append(f"<sub>{label}</sub>")
            else:
                lines.append(f"<sub>{label}</sub>")
            lines.append("")
            lines.append("---")
            lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"Written {out_path} ({total} jobs, {new_recent} new)")


def render_companies(jobs: list[dict], company_logos: dict[str, str], out_path: Path) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    company_summaries: dict[str, str] = {}
    if COMPANIES_CLASSIFIED_FILE.exists():
        for c in json.loads(COMPANIES_CLASSIFIED_FILE.read_text()):
            raw = c.get("summary", "")
            lines = [l for l in raw.splitlines() if not l.strip().startswith("#")]
            company_summaries[c["name"]] = " ".join(l.strip() for l in lines if l.strip())

    by_company: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        by_company[j["company"]].append(j)

    companies_sorted = sorted(by_company.keys(), key=str.casefold)
    total_jobs = len(jobs)
    new_recent = sum(1 for j in jobs if is_new_within(j, cutoff))

    lines = [
        "# Builder Jobs — By Company",
        "",
        (
            "Engineering roles grouped by company, linking directly to each company's job board."
            " Only companies with active openings are shown."
            " Listings older than 14 days are removed automatically."
        ),
        "",
        f"### **{len(companies_sorted)} companies** · **{total_jobs} open roles** ({new_recent} new)",
        "",
        "[← All roles](README.md) &nbsp;·&nbsp; [Remote only →](REMOTE.md) &nbsp;·&nbsp; [How it works →](https://github.com/zachproffitt/builder-jobs-scraper)",
        "",
        "<br>",
        "",
    ]

    for company in companies_sorted:
        company_jobs = by_company[company]
        domain = company_logos.get(company, "")
        summary = company_summaries.get(company, "")

        if domain:
            icon = f'<a href="https://{domain}"><img src="https://www.google.com/s2/favicons?domain={domain}&sz=32" width="16" height="16" align="absmiddle"></a>'
            heading = f"## {icon}&ensp;[{company}](https://{domain})"
        else:
            heading = f"## {company}"

        lines.append(heading)
        lines.append("")

        if summary:
            lines.append(summary)
            lines.append("")

        company_jobs_sorted = sorted(
            company_jobs,
            key=lambda j: (j["first_seen_at"] or j["first_seen"] or ""),
            reverse=True,
        )

        for j in company_jobs_sorted:
            meta = format_job_meta(j)
            is_new = is_new_within(j, cutoff)

            ts = j.get("first_seen_at", "")
            first_seen = j.get("first_seen", "")
            if ts:
                try:
                    date_str = datetime.fromisoformat(ts).strftime("%b %-d")
                except ValueError:
                    date_str = first_seen
            elif first_seen:
                try:
                    date_str = datetime.strptime(first_seen, "%Y-%m-%d").strftime("%b %-d")
                except ValueError:
                    date_str = first_seen
            else:
                date_str = ""

            new_str = f"{NEW_BADGE} " if is_new else ""
            meta_str = f" · {meta}" if meta else ""
            lines.append(f"- {new_str}[{j['title']}]({j['url']}){meta_str} ({date_str})")

        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"Written {out_path} ({len(companies_sorted)} companies, {total_jobs} jobs, {new_recent} new)")


def main():
    jobs, company_logos = collect_jobs()

    company_count = 0
    if COMPANIES_FILE.exists():
        companies = json.loads(COMPANIES_FILE.read_text())
        company_count = len([c for c in companies if c.get("ats") in SUPPORTED_ATS])

    render_index(
        jobs, company_logos, company_count,
        out_path=README,
        title="Builder Jobs",
        subtitle=(
            "A curated index of engineering roles from YC startups, VC-backed companies,"
            " and major tech — classified by Claude, updated hourly, and removed after 14 days."
            " Each listing links directly to the company's job board."
        ),
    )

    render_index(
        jobs, company_logos, company_count,
        out_path=REMOTE_README,
        title="Builder Jobs — Remote",
        subtitle=(
            "Remote engineering roles only — linking directly to each company's job board."
            " Classified by Claude, updated hourly, and removed after 14 days."
        ),
        remote_only=True,
    )

    render_companies(jobs, company_logos, out_path=COMPANIES_README)


if __name__ == "__main__":
    main()
