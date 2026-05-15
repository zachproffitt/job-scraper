#!/usr/bin/env python3
"""Generate README.md for the jobs repo by scanning all rendered job files."""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from badges import REMOTE_BADGE, HYBRID_BADGE, skill_badge
from render_jobs import clean_location, _company_logo_html

JOBS_REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent.parent / "jobs"
README = JOBS_REPO / "README.md"
REMOTE_README = JOBS_REPO / "REMOTE.md"
COMPANIES_FILE = Path(__file__).parent.parent / "data" / "companies.json"
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
        if md.name in ("README.md", "REMOTE.md"):
            continue
        fm = parse_frontmatter(md)
        if not fm.get("id"):
            continue
        skills_raw = fm.get("skills", "")
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else []
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
            "remote": fm.get("remote", "").strip() == "Remote",
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

    all_timestamps = [j["first_seen_at"] for j in jobs if j.get("first_seen_at")]
    last_run_str = ""
    if all_timestamps:
        last_run_dt = datetime.fromisoformat(max(all_timestamps))
        last_run_str = last_run_dt.strftime("%B %-d, %Y at %H:%M UTC")

    stats = f"**{total} open roles** ({new_today} new today)"
    if not remote_only:
        stats += f" &nbsp;·&nbsp; {company_count} companies searched"

    if remote_only:
        nav_links = "[← All roles](README.md) &nbsp;·&nbsp; [How it works →](https://github.com/zachproffitt/builder-jobs-scraper)"
    else:
        nav_links = "[How it works →](https://github.com/zachproffitt/builder-jobs-scraper) &nbsp;·&nbsp; [Remote only →](REMOTE.md)"

    lines = [
        f"# {title}",
        "",
        subtitle,
        "",
        f"### {stats}",
        "",
        nav_links,
        "",
        f"<sub>Last updated {last_run_str}</sub>" if last_run_str else "",
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


if __name__ == "__main__":
    main()
