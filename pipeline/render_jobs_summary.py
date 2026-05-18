#!/usr/bin/env python3
"""Write a GitHub Actions step summary for the pipeline run."""

import json
from datetime import datetime, timezone
from pathlib import Path

from render_common import SUPPORTED_ATS, strip_location_from_title, write_step_summary

DATA_DIR = Path(__file__).parent.parent / "data"


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    companies = json.loads((DATA_DIR / "companies.json").read_text())
    classified = json.loads((DATA_DIR / "jobs_classified.json").read_text())

    by_ats: dict[str, int] = {}
    for c in companies:
        ats = c.get("ats", "")
        if ats in SUPPORTED_ATS:
            by_ats[ats] = by_ats.get(ats, 0) + 1

    total_companies = sum(by_ats.values())

    raw_jobs_path = DATA_DIR / "jobs_raw.json"
    raw_jobs = json.loads(raw_jobs_path.read_text()) if raw_jobs_path.exists() else []

    renderable = [
        j for j in raw_jobs
        if classified.get(j["id"], {}).get("is_engineering") is True
        and not classified.get(j["id"], {}).get("is_contract", False)
        and classified.get(j["id"], {}).get("region") in ("us", "canada", "unclear")
    ]
    seen_groups: set[tuple[str, str]] = set()
    engineering = []
    for j in renderable:
        key = (j["company"], strip_location_from_title(j["title"]))
        if key not in seen_groups:
            seen_groups.add(key)
            engineering.append(j)

    new_today = [j for j in renderable if j.get("first_seen") == today]
    seen_new: set[tuple[str, str]] = set()
    new_today_deduped = []
    for j in new_today:
        key = (j["company"], strip_location_from_title(j["title"]))
        if key not in seen_new:
            seen_new.add(key)
            new_today_deduped.append(j)
    new_today = new_today_deduped

    log_path = DATA_DIR / "pipeline.log"
    log_lines = []
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if today in line:
                log_lines.append(line)

    lines = [
        f"## Pipeline run — {now}",
        "",
        f"**{len(engineering)}** engineering roles live &nbsp;·&nbsp; **{len(new_today)}** new today &nbsp;·&nbsp; **{total_companies}** companies searched",
        "",
        "### Companies by ATS",
        "| ATS | Companies |",
        "|---|---|",
    ]
    for ats, count in sorted(by_ats.items(), key=lambda x: -x[1]):
        lines.append(f"| {ats} | {count} |")

    if log_lines:
        lines += [
            "",
            f"### Errors ({len(log_lines)})",
            "```",
            *log_lines[-50:],
            "```",
        ]
    else:
        lines += ["", "No errors."]

    write_step_summary("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
