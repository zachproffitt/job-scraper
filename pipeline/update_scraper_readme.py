#!/usr/bin/env python3
"""Update the ATS company-count table and last-updated line in the scraper README."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMPANIES_FILE = ROOT / "data" / "companies.json"
README_FILE = ROOT / "README.md"

SUPPORTED_ATS = [
    ("ashby",           "Ashby",           "scrapers/ats_ashby.py"),
    ("greenhouse",      "Greenhouse",       "scrapers/ats_greenhouse.py"),
    ("lever",           "Lever",            "scrapers/ats_lever.py"),
    ("workday",         "Workday",          "scrapers/ats_workday.py"),
    ("bamboo",          "BambooHR",         "scrapers/ats_bamboo.py"),
    ("breezy",          "Breezy",           "scrapers/ats_breezy.py"),
    ("workable",        "Workable",         "scrapers/ats_workable.py"),
    ("smartrecruiters", "SmartRecruiters",  "scrapers/ats_smartrecruiters.py"),
    ("eightfold",       "Eightfold",        "scrapers/ats_eightfold.py"),
]


def main() -> None:
    companies = json.loads(COMPANIES_FILE.read_text())
    counts: dict[str, int] = {}
    for c in companies:
        ats = c.get("ats", "")
        if ats in {key for key, _, _ in SUPPORTED_ATS}:
            counts[ats] = counts.get(ats, 0) + 1

    total = sum(counts.get(key, 0) for key, _, _ in SUPPORTED_ATS)
    rows = ["| ATS | Companies | Scraper |", "|---|---|---|"]
    for key, label, scraper in SUPPORTED_ATS:
        count = counts.get(key, 0)
        rows.append(f"| {label} | {count} | `{scraper}` |")
    rows.append(f"| **Total** | **{total}** | |")
    new_table = "\n".join(rows)

    readme = README_FILE.read_text()

    # Update ATS table
    updated = re.sub(
        r"(## Supported ATS\n\n)(\| ATS.*?)(\n\n)",
        lambda m: m.group(1) + new_table + m.group(3),
        readme,
        flags=re.DOTALL,
    )

    # Only stamp last-updated if the table actually changed
    if updated != readme:
        now = datetime.now(timezone.utc).strftime("%-d %B %Y")
        updated = re.sub(
            r"(# Builder Jobs — Scraper\n\n)(\*Updated .*?\*\n\n)?",
            lambda m: m.group(1) + f"*Updated {now}*\n\n",
            updated,
        )
        README_FILE.write_text(updated)
        print("README updated")
    else:
        print("README unchanged")


if __name__ == "__main__":
    main()
