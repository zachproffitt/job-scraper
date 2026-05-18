#!/usr/bin/env python3
"""Generate README.md for the jobs repo by scanning all rendered job files."""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from badges import REMOTE_BADGE, HYBRID_BADGE, NEW_BADGE, skill_badge
from render_jobs import clean_location, _company_logo_html

JOBS_REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent.parent / "jobs"
README = JOBS_REPO / "README.md"
REMOTE_README = JOBS_REPO / "REMOTE.md"
COMPANIES_README = JOBS_REPO / "COMPANIES.md"
COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies.json"
COMPANIES_CLASSIFIED_FILE = Path(__file__).parent.parent / "data" / "companies_classified.json"
SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "bamboo", "breezy", "workable", "workday", "eightfold"}


def abbrev_comp(comp: str) -> str:
    """Normalize $100,000 → $100k for any currency with large numbers."""
    def shorten(m):
        n = int(m.group(0).replace(",", ""))
        return f"{n // 1000}k"
    return re.sub(r"\d{1,3}(?:,\d{3})+", shorten, comp)


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("<!--"):
        return {}
    end = text.find("-->")
    if end == -1:
        return {}
    meta_text = text[4:end]
    fm = {}
    for line in meta_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def format_meta(fm: dict) -> str:
    company = fm.get("company", "")
    location = fm.get("location", "").strip()
    remote = fm.get("remote", "").strip()
    hybrid = fm.get("hybrid", "").strip()
    level = fm.get("level", "").strip()
    comp = fm.get("comp", "").strip()
    comp_extras_raw = fm.get("comp_extras", "").strip()
    comp_extras = [s.strip() for s in comp_extras_raw.split(",") if s.strip()] if comp_extras_raw else []

    if " | " in location:
        location = location.split(" | ")[0].strip()
    if location in ("Not specified", ""):
        location = ""

    location = clean_location(location, is_remote=(remote == "Remote"))

    parts = [f"**{company}**"]
    if location:
        parts.append(location)
    if remote == "Remote":
        parts.append(REMOTE_BADGE)
    elif hybrid == "yes":
        parts.append(HYBRID_BADGE)
    if level and level not in ("unclear", ""):
        parts.append(f"`{level.capitalize()}`")
    if comp:
        parts.append(f"`{abbrev_comp(comp)}`")
    for extra in comp_extras:
        parts.append(f"`{extra.capitalize()}`")

    return " · ".join(parts)


def collect_jobs(jobs_repo: Path) -> tuple[list[dict], dict[str, str]]:
    """Scan all job .md files and return (jobs, company_logos)."""
    company_logos: dict[str, str] = {}
    if COMPANIES_FILE.exists():
        for c in json.loads(COMPANIES_FILE.read_text()):
            if c.get("website") and c.get("name"):
                domain = c["website"].removeprefix("https://").removeprefix("http://").split("/")[0]
                company_logos[c["name"]] = domain

    jobs = []
    for md in sorted(jobs_repo.rglob("*.md")):
        if md.name in ("README.md", "REMOTE.md", "COMPANIES.md"):
            continue
        fm = parse_frontmatter(md)
        if not fm.get("id"):
            continue
        skills_raw = fm.get("skills", "")
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else []
        comp_extras_raw = fm.get("comp_extras", "")
        comp_extras = [s.strip() for s in comp_extras_raw.split(",") if s.strip()] if comp_extras_raw else []
        remote_str = fm.get("remote", "").strip()
        location_raw = fm.get("location", "").strip()
        jobs.append({
            "title": fm.get("title", ""),
            "company": fm.get("company", ""),
            "meta": format_meta(fm),
            "summary": fm.get("summary", ""),
            "url": fm.get("url", ""),
            "skills": skills,
            "first_seen": fm.get("first_seen", "unknown"),
            "posted_at": fm.get("posted_at", ""),
            "first_seen_at": fm.get("first_seen_at", ""),
            "path": str(md.relative_to(jobs_repo)),
            "remote": remote_str == "Remote",
            "remote_str": remote_str,
            "hybrid": fm.get("hybrid", "").strip(),
            "location_raw": location_raw,
            "level": fm.get("level", "").strip(),
            "comp": fm.get("comp", "").strip(),
            "comp_extras": comp_extras,
        })
    return jobs, company_logos


def render_index(jobs: list[dict], company_logos: dict[str, str], company_count: int,
                 out_path: Path, title: str, subtitle: str, remote_only: bool = False) -> None:
    today = datetime.now().strftime("%Y-%m-%d")

    if remote_only:
        jobs = [j for j in jobs if j["remote"]]

    by_date: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        by_date[j["first_seen"]].append(j)

    total = sum(len(v) for v in by_date.values())
    new_today = len(by_date.get(today, []))

    stats = f"**{total} open roles** ({new_today} new today)"
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
            lines.append(f"### [{j['title']}]({j['path']})")
            domain = company_logos.get(j["company"], "")
            logo = _company_logo_html(domain)
            lines.append(f"{logo}{j['meta']}")
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
    print(f"Written {out_path} ({total} jobs, {new_today} new today)")


def format_job_meta(j: dict) -> str:
    """Build the location · remote · level · comp inline string for a job list entry."""
    parts = []
    location = j["location_raw"]
    if " | " in location:
        location = location.split(" | ")[0].strip()
    if location and location != "Not specified":
        location = clean_location(location, is_remote=(j["remote_str"] == "Remote"))
        if location:
            parts.append(location)
    if j["remote_str"] == "Remote":
        parts.append(REMOTE_BADGE)
    elif j["hybrid"] == "yes":
        parts.append(HYBRID_BADGE)
    level = j["level"]
    if level and level not in ("unclear", ""):
        parts.append(f"`{level.capitalize()}`")
    if j["comp"]:
        parts.append(f"`{abbrev_comp(j['comp'])}`")
    for extra in j["comp_extras"]:
        parts.append(f"`{extra.capitalize()}`")
    return " · ".join(parts)


def render_companies(jobs: list[dict], company_logos: dict[str, str], out_path: Path) -> None:
    today = datetime.now().strftime("%Y-%m-%d")

    company_summaries: dict[str, str] = {}
    if COMPANIES_CLASSIFIED_FILE.exists():
        for c in json.loads(COMPANIES_CLASSIFIED_FILE.read_text()):
            company_summaries[c["name"]] = c.get("summary", "")

    by_company: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        by_company[j["company"]].append(j)

    companies_sorted = sorted(by_company.keys(), key=str.casefold)
    total_jobs = len(jobs)
    new_today = sum(1 for j in jobs if j["first_seen"] == today)

    lines = [
        "# Builder Jobs — By Company",
        "",
        (
            "Engineering roles grouped by company and sorted alphabetically."
            " Only companies with active openings are shown."
            " Listings older than 14 days are removed automatically."
        ),
        "",
        f"### **{len(companies_sorted)} companies** · **{total_jobs} open roles** ({new_today} new today)",
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
        role_label = f"{len(company_jobs)} role{'s' if len(company_jobs) != 1 else ''}"

        if domain:
            icon = f'<a href="https://{domain}"><img src="https://www.google.com/s2/favicons?domain={domain}&sz=32" width="16" height="16" align="absmiddle"></a>'
            heading = f"## {icon}&ensp;[{company}](https://{domain}) · {role_label}"
        else:
            heading = f"## {company} · {role_label}"

        lines.append(heading)
        lines.append("")

        if summary:
            lines.append(f"> {summary}")
            lines.append("")

        company_jobs_sorted = sorted(
            company_jobs,
            key=lambda j: (j["first_seen_at"] or j["first_seen"] or ""),
            reverse=True,
        )

        for j in company_jobs_sorted:
            meta = format_job_meta(j)
            is_new = j["first_seen"] == today

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

            new_str = f" {NEW_BADGE}" if is_new else ""
            meta_str = f" · {meta}" if meta else ""
            lines.append(f"- [{j['title']}]({j['path']}){meta_str}{new_str} ({date_str})")

        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"Written {out_path} ({len(companies_sorted)} companies, {total_jobs} jobs, {new_today} new today)")


def main():
    if not JOBS_REPO.exists():
        print(f"Jobs repo not found: {JOBS_REPO}")
        sys.exit(1)

    jobs, company_logos = collect_jobs(JOBS_REPO)

    company_count = 0
    if COMPANIES_FILE.exists():
        companies = json.loads(COMPANIES_FILE.read_text())
        company_count = len([c for c in companies if c.get("ats") in SUPPORTED_ATS])

    render_index(
        jobs, company_logos, company_count,
        out_path=README,
        title="Builder Jobs",
        subtitle=(
            "For engineers who build. Roles are scraped hourly from YC startups, VC-backed companies,"
            " and major tech curated across 20+ industries — classified by Claude, and removed after 14 days."
        ),
    )

    render_index(
        jobs, company_logos, company_count,
        out_path=REMOTE_README,
        title="Builder Jobs — Remote",
        subtitle=(
            "Remote engineering roles only, scraped hourly and classified by Claude."
            " Listings older than 14 days are removed automatically."
        ),
        remote_only=True,
    )

    render_companies(jobs, company_logos, out_path=COMPANIES_README)


if __name__ == "__main__":
    main()
