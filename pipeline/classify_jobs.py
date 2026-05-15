#!/usr/bin/env python3
"""Classify jobs as builder engineering roles and generate summaries."""

import hashlib
import html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from log import log_error as _log_error
from llm import BACKEND, CLAUDE_MODEL, OLLAMA_MODEL, call_claude as _call_claude, call_ollama as _call_ollama

JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "jobs_classified.json"
LOG_FILE = Path(__file__).parent.parent / "data" / "pipeline.log"

WORKERS = 5 if BACKEND == "claude" else 2
SAVE_EVERY = 100

SYSTEM_PROMPT = """\
You are filtering a job board for software engineers — people who primarily write code.

INCLUDE — person primarily writes code:
- Software engineers of all kinds (backend, frontend, mobile, infrastructure, platform, SRE, DevOps)
- Data engineers building pipelines, ETL systems, data infrastructure
- ML/AI engineers building models, training infrastructure, inference systems
- Security engineers building security systems and tooling
- QA/test engineers writing automation and test infrastructure
- Firmware and embedded software engineers (writing code that runs on hardware)
- Kernel and systems software engineers
- Engineering managers leading teams of software engineers
- Researchers who primarily build novel models or software systems (e.g., at AI/ML labs)
- Data scientists who primarily build and train models, not just analyze data
- Analytics engineers building data pipelines and warehouse infrastructure
- Forward deployed engineers embedded at client sites writing and deploying software

EXCLUDE — person is not primarily writing code:
- Sales, marketing, HR, recruiting, finance, legal, operations
- Solutions engineers and sales engineers (customer-facing, not building)
- Technical program managers (coordinating, not coding)
- Developer advocates and developer relations
- Product managers and product designers
- Hardware engineers (electrical, mechanical, PCB design, RF, optical, systems integration of physical components)
- Electrical engineers working on subsystems, power, or manufacturing
- Manufacturing, process, and production engineers
- Any title containing "analyst" without also containing "engineer" — data analyst,
  business analyst, product analyst, operations analyst, marketing analyst, etc.
  (Analytics Engineer and Data Engineer stay in; Data Analyst is out)
- Research roles that are primarily analytical rather than building software systems

For borderline cases where the title doesn't resolve it, use the description:
ask "Will this person primarily write code?" — if yes, BUILDER; if no or unclear, exclude.
A firmware engineer writes code. A hardware engineer designs circuits or physical components — exclude them.
An electrical engineer working on subsystems, power, or manufacturing is a hardware engineer — exclude them.
A "Technical Mission Designer" or "Technical Designer" in game dev is a designer who uses scripts — exclude them.
A "Technical Animator" is borderline — include only if the description is primarily about building animation systems in code.
"Applied Scientist" and "Research Scientist" at tech or AI companies are borderline — include if the description involves training models, building systems, or writing production code; exclude if primarily publishing research or doing data analysis without building.
"Solutions Architect" is borderline — include only if the description clearly involves writing code or building systems; exclude if primarily designing solutions for customers without implementation responsibility.
"Technical Lead" and "Tech Lead" — include; they write code and lead a team.
"Systems Engineer" is ambiguous by domain — include for software systems, platform, or embedded firmware; exclude for aerospace, defense hardware, mechanical, or RF/optical systems.
"Site Reliability Engineer" and "SRE" — always include.

For each job posting provided, extract the following fields. Use judgment — if the description gives strong signals, use them even if indirect.

1. BUILDER: yes / no / unclear
   yes = will primarily write code or build systems
   no = will not primarily write code
   unclear = description doesn't make it possible to determine

2. SUMMARY (only if BUILDER is yes): 1-2 sentences in imperative active voice starting with a verb.
   Sentence 1: What you'll build — name the specific system, product, or infrastructure.
   Sentence 2 (optional): Add only if it gives meaningful signal — key technical challenge, scale, unique domain (e.g. defense, climate), or access requirement (e.g. clearance, must be on-site). Do NOT restate years of experience or generic requirements. Skip if nothing meaningful to add.
   No perks, no culture. If too vague to summarize honestly, write: vague

3. SKILLS (only if BUILDER is yes): up to 8 specific technologies, languages, tools, frameworks, or notable requirements.
   - Extract regardless of phrasing: "experience with tools such as Python" → Python; "familiarity with Go preferred" → Go
   - Include specific tech: PyTorch, Rust, PostgreSQL, Kubernetes, React, AWS, Terraform, CUDA, ROS2
   - Include education if notable: "PhD Required", "PhD Preferred"
   - Include clearance if required: "TS/SCI Clearance", "Security Clearance"
   - Skip pure generics: "backend", "APIs", "the cloud" — but "AWS", "GCP", "Azure" are fine
   - Domain terms only when specific: "Distributed Systems" alone is too vague; "Kafka", "Raft Consensus" are fine
   - Never include skills that apply to every software engineer: "Coding", "Problem Solving", "Software Development", "Software Engineering", "Programming", "Multiple Programming Languages", or any phrasing that just means "writes code".
   - Never name a category instead of a skill: "Multiple Programming Languages" is not a skill — pick the actual languages. "Various frameworks" is not a skill — pick the framework.
   - Never list a sub-feature alongside its parent: if "Kotlin" is listed, do not also list "Kotlin Coroutines" or "Kotlin Flow"; if "React" is listed, do not also list "React Hooks". Same rule applies to any language or framework and its sub-libraries.
   - Architecture and design patterns (MVVM, MVC, MVI, Redux, Clean Architecture, Microservices) are too generic — skip them unless the description singles one out as the defining technical challenge.
   - After listing, remove skills that are multiple subcategories of the same concept (e.g. if the role is in security, pick at most 2 specific technologies — not "Security Architecture", "Threat Modeling", "Secure by Design", "Platform Security" all at once).
   - Prefer breadth: if skills cluster in one domain (e.g. all Android, all ML frameworks), pick the 1-2 most specific and use remaining slots for other aspects of the role.
   - Use proper capitalization: official casing for tech names (Python, PyTorch, PostgreSQL, JavaScript, AWS, GCP), Title Case for other terms (Distributed Systems, Machine Learning, Computer Vision).
   If none remain after filtering, write: n/a

4. LEVEL: Seniority of this role. Title keyword takes priority:
   "Intern"/"Co-op" → intern
   "Junior"/"Associate"/"Entry" → junior
   "Senior" → senior
   "Staff" → staff
   "Principal" → principal
   "Manager"/"Director" → manager
   No title keyword → use years of experience from description:
   0-2 → junior, 2-5 → mid, 5-10 → senior, 10+ → staff
   If no signal at all → unclear

   Important: "Member of Technical Staff" and "Member of the Technical Staff" are job title conventions at some companies, not seniority indicators — ignore "Staff" in that phrase. Look for a seniority keyword elsewhere in the title (e.g. "Member of Technical Staff, Senior" → senior) or fall back to experience years.

   Respond with exactly one of: intern / junior / mid / senior / staff / principal / manager / unclear

5. CONTRACT: Is this a contract, temporary, or fixed-term position rather than permanent full-time employment?
   yes = contract, contractor, freelance, fixed-term, temporary, limited-term engagement
   no = permanent full-time employment (default if not stated)
   Ignore domain uses of "contract" (e.g. "smart contracts", "government contracts").
   Respond with exactly one of: yes / no

6. HYBRID: Does this role require some in-office days while also allowing some remote work?
   yes = the description requires in-office days alongside remote work — "X days in office per week/month", "hybrid", or equivalent phrasing. Description content takes priority over location labels: if the description requires in-office time, mark yes even if the posting or title says "Remote".
   no = fully remote (no in-office requirement stated in the description), fully on-site, or work arrangement not mentioned
   Respond with exactly one of: yes / no

7. COMP: Base salary range stated in the posting.
   Preserve the original currency symbol (e.g. "$120k-$160k", "£75k-£100k", "€80k-€110k").
   Abbreviate thousands as k (e.g. £75,000 → £75k). If stated in full dollars/pounds/etc, keep as-is.
   If multiple ranges by location, use the overall min to overall max.
   If only a single figure, use that. If not stated, write: n/a

8. COMP_EXTRAS: Non-salary compensation worth calling out — equity or bonus only.
   Use exactly "equity" for any equity/stock/RSU/options, and exactly "bonus" for any bonus type.
   Do not include standard benefits (401k, health insurance, PTO). If none, write: n/a

9. REGION: Where must the candidate be located to take this job?
   us = role is based in the US, or remote with no geographic restriction, or explicitly open to US candidates
   canada = role is based in Canada or remote open to Canada (and possibly US); not available to US-only candidates
   international = requires presence or work authorization in a non-US, non-Canada country; or on-site outside North America
   unclear = cannot determine from the posting
   Use the description to override location labels: "Remote" with no restriction → us; "Remote - UK" with no mention of US eligibility → international.
   Respond with exactly one of: us / canada / international / unclear

10. LOCATION: Normalized display location. Use the description to confirm or correct the ATS location field.
   US on-site: "City, ST" using 2-letter state code (e.g. "Boulder, CO" · "New York, NY" · "San Francisco, CA")
   US remote: "Remote"
   International on-site: "City, Country" (e.g. "London, UK" · "Berlin, Germany" · "Toronto, Canada")
   Multiple locations: join with " / " (e.g. "New York, NY / Remote" · "San Francisco, CA / New York, NY")
   If location cannot be determined from the posting: n/a

Respond in exactly this format:
BUILDER: <yes/no/unclear>
SUMMARY: <summary or vague or n/a>
SKILLS: <skill1, skill2, ... or n/a>
LEVEL: <intern/junior/mid/senior/staff/principal/manager/unclear>
CONTRACT: <yes/no>
HYBRID: <yes/no>
COMP: <$Xk-$Yk or n/a>
COMP_EXTRAS: <extras or n/a>
REGION: <us/canada/international/unclear>
LOCATION: <City, ST or City, Country or Remote or n/a>
"""

# Description first (long content before query improves accuracy), then identifiers
USER_TEMPLATE = """\
<job>
<description>
{description}
</description>
<title>{title}</title>
<company>{company}</company>
</job>"""

OLLAMA_TEMPLATE = "/no_think\n" + SYSTEM_PROMPT + "\n" + USER_TEMPLATE


def content_hash(job: dict) -> str:
    key = f"{job['id']}:{job['title']}:{job.get('raw_text', '')[:200]}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


VALID_LEVELS = {"intern", "junior", "mid", "senior", "staff", "principal", "manager"}


VALID_REGIONS = {"us", "canada", "international", "unclear"}


def parse_response(text: str) -> dict:
    result = {
        "is_engineering": None,
        "is_contract": False,
        "is_hybrid": False,
        "region": "unclear",
        "location": None,
        "job_summary": None,
        "skills": [],
        "level": None,
        "comp": None,
        "comp_extras": [],
    }

    for line in text.splitlines():
        if line.startswith("BUILDER:"):
            val = line.removeprefix("BUILDER:").strip().lower()
            if val == "yes":
                result["is_engineering"] = True
            elif val == "no":
                result["is_engineering"] = False
        elif line.startswith("SUMMARY:"):
            val = line.removeprefix("SUMMARY:").strip()
            if val.lower() not in ("n/a", "vague", ""):
                result["job_summary"] = val
        elif line.startswith("SKILLS:"):
            val = line.removeprefix("SKILLS:").strip()
            if val.lower() != "n/a":
                result["skills"] = [s.strip() for s in re.split(r",\s*(?![^(]*\))", val) if s.strip()][:8]
        elif line.startswith("LEVEL:"):
            val = line.removeprefix("LEVEL:").strip().lower()
            if val in VALID_LEVELS:
                result["level"] = val
        elif line.startswith("CONTRACT:"):
            val = line.removeprefix("CONTRACT:").strip().lower()
            result["is_contract"] = val == "yes"
        elif line.startswith("HYBRID:"):
            val = line.removeprefix("HYBRID:").strip().lower()
            result["is_hybrid"] = val == "yes"
        elif line.startswith("COMP:"):
            val = line.removeprefix("COMP:").strip()
            if val.lower() != "n/a":
                result["comp"] = val
        elif line.startswith("COMP_EXTRAS:"):
            val = line.removeprefix("COMP_EXTRAS:").strip()
            if val.lower() != "n/a":
                result["comp_extras"] = [s.strip() for s in val.split(",") if s.strip()]
        elif line.startswith("REGION:"):
            val = line.removeprefix("REGION:").strip().lower()
            if val in VALID_REGIONS:
                result["region"] = val
        elif line.startswith("LOCATION:"):
            val = line.removeprefix("LOCATION:").strip()
            if val.lower() not in ("n/a", ""):
                result["location"] = val

    return result


# Token bucket rate limiter — stays under the 50k input tokens/minute org limit.
# Estimate per request: ~1500 tokens (cached system prompt counts at 10% = 250,
# plus ~1250 average user message). Target 48k/min to leave headroom.
_RATE_LIMIT_TOKENS_PER_MIN = 48_000
_TOKENS_PER_REQUEST = 1_500
_rate_lock = threading.Lock()
_rate_tokens = float(_RATE_LIMIT_TOKENS_PER_MIN)
_rate_last_refill = time.monotonic()


def _acquire_rate_limit() -> None:
    global _rate_tokens, _rate_last_refill
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _rate_last_refill
        _rate_tokens = min(
            float(_RATE_LIMIT_TOKENS_PER_MIN),
            _rate_tokens + elapsed / 60.0 * _RATE_LIMIT_TOKENS_PER_MIN,
        )
        _rate_last_refill = now
        if _rate_tokens < _TOKENS_PER_REQUEST:
            wait = (_TOKENS_PER_REQUEST - _rate_tokens) / (_RATE_LIMIT_TOKENS_PER_MIN / 60.0)
            _rate_tokens = 0.0
        else:
            _rate_tokens -= _TOKENS_PER_REQUEST
            wait = 0.0
    if wait > 0:
        time.sleep(wait)


def log_error(message: str) -> None:
    _log_error("classify_jobs", message, LOG_FILE)


def call_claude(system: str, user_message: str) -> str:
    _acquire_rate_limit()
    return _call_claude(system, user_message, max_tokens=512, log_error=log_error)


def call_ollama(prompt: str) -> str:
    return _call_ollama(prompt, num_ctx=4096)


def classify_with_llm(job: dict) -> dict:
    description = html.unescape(job.get("raw_text", "")).strip()
    if BACKEND == "ollama":
        prompt = OLLAMA_TEMPLATE.format(
            title=job["title"],
            company=job["company"],
            description=description,
        )
        text = call_ollama(prompt)
    else:
        user_message = USER_TEMPLATE.format(
            title=job["title"],
            company=job["company"],
            description=description,
        )
        text = call_claude(SYSTEM_PROMPT, user_message)
    return parse_response(text)


def main():
    jobs = json.loads(JOBS_FILE.read_text())

    existing: dict[str, dict] = {}
    if OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text())

    classify_all = "--all" in sys.argv
    today = datetime.now(timezone.utc).date().isoformat()

    def needs_work(j: dict) -> bool:
        ex = existing.get(j["id"])
        if ex and ex.get("source_hash") == content_hash(j) and not classify_all:
            return False
        return (
            classify_all
            or j.get("first_seen") == today
            or j["id"] in existing
        )

    with_desc = [
        j for j in jobs
        if j.get("raw_text", "").strip() and needs_work(j)
    ]
    without_desc = sum(1 for j in jobs if not j.get("raw_text", "").strip())

    print(f"Backend: {BACKEND} ({'Claude ' + CLAUDE_MODEL if BACKEND == 'claude' else 'Ollama ' + OLLAMA_MODEL})")
    print(f"{len(with_desc)} jobs to classify today, {without_desc} skipped (no description)")
    print(f"Workers: {WORKERS}\n")

    if not with_desc:
        print("Nothing to classify. Run fetch_job_descriptions.py first if jobs are missing descriptions.")
        return

    eng = not_eng = unclear = errors = 0
    lock = threading.Lock()
    completed = 0
    total = len(with_desc)

    def process(job: dict) -> tuple:
        return job, classify_with_llm(job)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_to_job = {executor.submit(process, job): job for job in with_desc}

        for future in as_completed(future_to_job):
            with lock:
                completed += 1
                n = completed

            try:
                job, cl = future.result()
            except Exception as e:
                job = future_to_job[future]
                with lock:
                    errors += 1
                msg = f"{job['company']}: {job['title'][:50]} — {e}"
                print(f"  [{n:>5}/{total}] ERROR {msg}")
                log_error(f"job error: {msg}")
                continue

            with lock:
                existing[job["id"]] = {
                    "is_engineering": cl["is_engineering"],
                    "is_contract": cl["is_contract"],
                    "is_hybrid": cl["is_hybrid"],
                    "region": cl["region"],
                    "location": cl["location"],
                    "job_summary": cl["job_summary"],
                    "skills": cl["skills"],
                    "level": cl["level"],
                    "comp": cl["comp"],
                    "comp_extras": cl["comp_extras"],
                    "source_hash": content_hash(job),
                }
                is_e = cl["is_engineering"]
                if is_e is True:
                    eng += 1
                    contract_tag = " [contract]" if cl["is_contract"] else ""
                    summary = cl["job_summary"] or "no summary"
                    line = f"  [{n:>5}/{total}] ✓ {job['company']}: {job['title'][:50]}{contract_tag} — {summary[:60]}"
                elif is_e is False:
                    not_eng += 1
                    line = f"  [{n:>5}/{total}] ✗ {job['company']}: {job['title'][:50]}"
                else:
                    unclear += 1
                    line = f"  [{n:>5}/{total}] ? {job['company']}: {job['title'][:50]}"

                print(line)

                if n % SAVE_EVERY == 0:
                    OUTPUT_FILE.write_text(json.dumps(existing, indent=2))
                    print(f"  [checkpoint] saved {n}/{total}")

    OUTPUT_FILE.write_text(json.dumps(existing, indent=2))
    total_eng = sum(1 for v in existing.values() if v.get("is_engineering") is True)

    print(f"\nThis run — builder: {eng}, not: {not_eng}, unclear: {unclear}, errors: {errors}")
    print(f"Written to {OUTPUT_FILE}")
    print(f"Total builder roles in cache: {total_eng}/{len(existing)}")


if __name__ == "__main__":
    main()
