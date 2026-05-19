#!/usr/bin/env python3
"""Write a GitHub Actions step summary for the pipeline run."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from render_common import SUPPORTED_ATS, strip_location_from_title, is_new_within, write_step_summary
from llm import (
    CLAUDE_PRICE_INPUT, CLAUDE_PRICE_OUTPUT,
    CLAUDE_PRICE_CACHE_WRITE, CLAUDE_PRICE_CACHE_READ,
    estimate_cost,
)

DATA_DIR = Path(__file__).parent.parent / "data"


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

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

    new_recent = [j for j in renderable if is_new_within(j, cutoff)]
    seen_new: set[tuple[str, str]] = set()
    new_recent_deduped = []
    for j in new_recent:
        key = (j["company"], strip_location_from_title(j["title"]))
        if key not in seen_new:
            seen_new.add(key)
            new_recent_deduped.append(j)
    new_recent = new_recent_deduped

    log_path = DATA_DIR / "jobs.log"
    log_lines = []
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if today in line:
                log_lines.append(line)

    stats_path = DATA_DIR / "jobs_pipeline_stats.json"
    classify_stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}

    lines = [
        f"## Pipeline run — {now}",
        "",
        f"**{len(engineering)}** engineering roles live &nbsp;·&nbsp; **{len(new_recent)}** new &nbsp;·&nbsp; **{total_companies}** companies searched",
        "",
        "### Companies by ATS",
        "| ATS | Companies |",
        "|---|---|",
    ]
    for ats, count in sorted(by_ats.items(), key=lambda x: -x[1]):
        lines.append(f"| {ats} | {count} |")

    if classify_stats:
        classified = classify_stats.get("classified", 0)
        errors = classify_stats.get("errors", 0)
        deferred = classify_stats.get("deferred", 0)
        requests = classify_stats.get("requests", 0)
        input_tok = classify_stats.get("input_tokens", 0)
        output_tok = classify_stats.get("output_tokens", 0)
        cache_write = classify_stats.get("cache_creation_input_tokens", 0)
        cache_read = classify_stats.get("cache_read_input_tokens", 0)

        cost = estimate_cost(classify_stats)

        status_parts = [f"**{classified}** classified"]
        if errors:
            status_parts.append(f"**{errors}** errors")
        if deferred:
            status_parts.append(f"**{deferred}** deferred to next run")

        lines += [
            "",
            f"### Classification — {' · '.join(status_parts)}",
        ]

        if requests:
            per_job_cost = cost / requests
            lines += [
                "",
                f"| | Tokens | Rate | Cost |",
                f"|---|---|---|---|",
                f"| Input | {input_tok:,} | ${CLAUDE_PRICE_INPUT}/1M | ${input_tok * CLAUDE_PRICE_INPUT / 1_000_000:.3f} |",
                f"| Cache read | {cache_read:,} | ${CLAUDE_PRICE_CACHE_READ}/1M | ${cache_read * CLAUDE_PRICE_CACHE_READ / 1_000_000:.3f} |",
                f"| Cache write | {cache_write:,} | ${CLAUDE_PRICE_CACHE_WRITE}/1M | ${cache_write * CLAUDE_PRICE_CACHE_WRITE / 1_000_000:.3f} |",
                f"| Output | {output_tok:,} | ${CLAUDE_PRICE_OUTPUT}/1M | ${output_tok * CLAUDE_PRICE_OUTPUT / 1_000_000:.3f} |",
                f"| **Total** | | | **${cost:.3f}** |",
                "",
                f"Avg per job: {input_tok // requests:,} input + {cache_read // requests:,} cache read + {output_tok // requests:,} output tokens &nbsp;·&nbsp; **${per_job_cost:.4f}/job**",
            ]

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
