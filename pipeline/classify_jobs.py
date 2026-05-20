#!/usr/bin/env python3
"""Classify jobs as builder engineering roles and generate summaries."""

import hashlib
import html
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from log import log_error as _log_error
from llm import BACKEND, CLAUDE_MODEL, OLLAMA_MODEL, chat, get_usage, estimate_cost

JOBS_FILE = Path(__file__).parent.parent / "data" / "jobs_raw.json"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "jobs_classified.json"
TITLE_SKIP_FILE = Path(__file__).parent.parent / "data" / "job_title_skip_patterns.json"
LOG_FILE = Path(__file__).parent.parent / "data" / "jobs.log"

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
- Kernel, compiler, and systems software engineers
- Engineering managers leading teams of software engineers
- Researchers who primarily build novel models or software systems (e.g., at AI/ML labs)
- Data scientists who primarily build and train models, not just analyze data
- Analytics engineers building data pipelines and warehouse infrastructure
- Forward deployed engineers embedded at client sites writing and deploying software
- Quantitative developers building trading systems, risk models, or execution infrastructure
- Computational biologists and bioinformaticians writing code to build analysis pipelines or models
- Game engine programmers, graphics engineers, and rendering engineers writing low-level systems
- "Founding Engineer" roles at early-stage startups — hands-on engineering, include

EXCLUDE — person is not primarily writing code:
- Sales, marketing, HR, recruiting, finance, legal, operations
- Solutions engineers and sales engineers (customer-facing, not building)
- Technical program managers (coordinating, not coding)
- Technical account managers and customer success engineers (managing relationships, not building)
- Developer advocates and developer relations (advocacy and content, not building)
- Product managers and product designers
- Hardware engineers (electrical, mechanical, PCB design, RF, optical, systems integration of physical components)
- Electrical engineers working on subsystems, power, or manufacturing
- Manufacturing, process, and production engineers
- Scrum masters, agile coaches, and project managers
- "Head of Data" or "Head of Analytics" roles that are organizational/strategic without hands-on coding
- Any title containing "analyst" without also containing "engineer" — data analyst,
  business analyst, product analyst, operations analyst, marketing analyst, etc.
  (Analytics Engineer and Data Engineer stay in; Data Analyst is out)
- Research roles that are primarily analytical rather than building software systems

For borderline cases where the title doesn't resolve it, use the description:
ask "Will this person primarily write code?" — if yes, BUILDER; if no or unclear, exclude.

Additional borderline guidance:
- "Technical Mission Designer" or "Technical Designer" in game dev is a designer who uses scripts — exclude.
- "Technical Animator" — include only if the description is primarily about building animation systems in code.
- "Technical Recruiter" — exclude (recruiting, not coding).
- "Technical Writer" — exclude (documentation, not coding).
- "Applied Scientist" and "Research Scientist" at tech or AI companies — include if the description involves training models, building systems, or writing production code; exclude if primarily publishing research or doing data analysis without building.
- "Solutions Architect" — include only if the description clearly involves writing code or building systems; exclude if primarily designing solutions for customers without implementation responsibility.
- "Systems Engineer" — include for software systems, platform, or embedded firmware; exclude for aerospace, defense hardware, mechanical, or RF/optical systems.
- "Developer Experience" / "DX Engineer" — include if primarily writing SDKs, tooling, or internal developer infrastructure; exclude if primarily advocacy, documentation, or content.
- "Lead Software Engineer" / "Lead Engineer" / "Tech Lead" — these are IC roles with project leadership; BUILDER: yes, LEVEL: senior.
- "VP of Engineering" or "Head of Engineering" — BUILDER: yes (engineering manager), LEVEL: manager.
- "CTO" — use the description; at early startups often writes code (BUILDER: yes); at large companies typically organizational (BUILDER: no or unclear).
- "Staff Product Manager", "Staff Designer", "Principal Consultant" — exclude unless the title is clearly an engineering role.
- "Data Scientist" — the description is decisive: include if training models, building ML infrastructure, or writing production code; exclude if primarily SQL queries, dashboards, or presenting findings without building systems.
- "Quantitative Researcher" at finance firms — include if building trading systems or execution infrastructure in code; exclude if primarily mathematical modeling without implementation.

For each job posting, extract the following fields. Use judgment from the description even when signals are indirect.

1. BUILDER: yes / no / unclear
   yes = will primarily write code or build systems
   no = will not primarily write code
   unclear = description doesn't make it possible to determine

2. SUMMARY (only if BUILDER is yes): 1-2 sentences in imperative active voice starting with a verb.
   Sentence 1: What you'll build — name the specific system, product, or infrastructure.
   Sentence 2 (optional): Add only if it gives meaningful signal — key technical challenge, scale, unique domain (e.g. defense, climate), or access requirement (e.g. clearance, must be on-site). Do NOT restate years of experience or generic requirements. Skip if nothing meaningful to add.
   No perks, no culture. Avoid summaries generic enough to describe any engineering role ("Build software products for customers"). If too vague to summarize honestly, write: vague

   Examples of good summaries:
   - "Build the real-time data ingestion pipeline for financial market data across 50+ exchanges."
   - "Develop autonomous navigation algorithms for warehouse robots operating at millions of sq ft of floor space."
   - "Own the compiler toolchain for a new systems programming language; requires on-site in Austin, TX."
   - "Build ML training infrastructure and model serving systems for large language models; requires active TS/SCI clearance."

   Examples of bad summaries (write: vague instead):
   - "Build software products for enterprise customers." (no specificity)
   - "Work on the full stack of our platform." (could describe any company)
   - "Contribute to exciting projects across the engineering organization." (meaningless)

3. SKILLS (only if BUILDER is yes): up to 8 specific technologies, languages, tools, frameworks, or notable requirements.
   Core skills only — what someone uses to build, not what they're required to know about.
   - Extract regardless of phrasing: "experience with tools such as Python" → Python; "familiarity with Go preferred" → Go
   - Include specific tech: PyTorch, Rust, PostgreSQL, Kubernetes, React, AWS, Terraform, CUDA, ROS2
   - Include notable degree levels: "PhD Required", "PhD Preferred". Degree subjects ("Computer Science", "Computer Engineering") are not skills — every applicant has one.
   - Include clearance if required: "TS/SCI Clearance", "Security Clearance"

   Skip these — too generic or not actually a skill:
   - "Coding", "Programming", "Software Development", "Multiple Programming Languages", "Various Frameworks", "backend", "APIs", "the cloud"
   - "Git" / "version control" — universal, skip
   - "Linux" — skip unless the role is specifically kernel or systems-level Linux work
   - "REST" / "RESTful APIs" — skip; every backend engineer works with REST
   - "Agile", "Scrum", "Kanban", "Jira" — process, not a skill
   - "CI/CD" alone — too generic; prefer specific tools: GitHub Actions, Jenkins, CircleCI, ArgoCD
   - "Machine Learning" alone — too vague; prefer PyTorch, TensorFlow, JAX, scikit-learn, Hugging Face
   - "Cloud" alone — too vague; prefer AWS, GCP, Azure
   - "Databases" alone — too vague; prefer PostgreSQL, MySQL, MongoDB, Redis, DynamoDB
   - "Object-Oriented Programming" / "OOP" / "design patterns" — foundational, skip
   - "Microservices", "MVVM", "MVC", "Redux", "Clean Architecture" — architecture patterns, skip unless the defining technical challenge
   - "Documentation" — not a tech skill
   - Standards and compliance: "ISO 27001", "OWASP Top 10", "GDPR", "SOC 2", "PCI-DSS"

   - Domain terms only when specific: "Distributed Systems" alone is too vague; "Kafka", "Raft Consensus" are fine
   - Reduce redundancy: don't list sub-features alongside their parent (no "Kotlin Coroutines" if "Kotlin" is listed; no "React Hooks" if "React" is listed); don't list 3+ items from the same ecosystem (pick the 1-2 most distinctive and use remaining slots for other aspects of the role)
   - Consistent casing: official casing for tech names (Python, PyTorch, PostgreSQL, JavaScript, AWS, GCP); Title Case for descriptive terms (Distributed Systems, Machine Learning, Computer Vision, Game Engine Development)

   Include these when they appear (specific enough to be meaningful):
   - Niche or newer languages: Zig, Mojo, Julia, Chapel, Elixir, Erlang, OCaml
   - Data stack tools: dbt, Airflow, Spark, Databricks, Snowflake, BigQuery, Redshift, Fivetran
   - Robotics: ROS, ROS2, SLAM, MoveIt
   - Security tooling: Ghidra, IDA Pro, Burp Suite, Wireshark, pwndbg (specific reverse engineering / offensive security tools)
   - Observability: Datadog, Prometheus, Grafana, OpenTelemetry (when central to the role, not just mentioned)
   - Prefer the framework over the language when both are listed and the framework is the real signal: "Next.js" over "JavaScript"; "FastAPI" over "Python"

   If none remain after filtering, write: n/a

   Domain-specific BUILDER notes:
   - Defense/government contractors: "Systems Engineer" ranges from software integration (include) to avionics/RF hardware (exclude) — use the description carefully. "Mission Systems Software Engineer" → include.
   - Biotech/pharma: "Scientist" usually excludes (wet lab), but "Computational Scientist", "Software Scientist", or roles centered on simulation/analysis pipelines include.
   - Fintech/trading: "Quantitative Analyst" usually excludes (mathematical research); "Quantitative Developer" or "Quantitative Engineer" includes (building trading systems in code).
   - Gaming: "Technical Designer" and "Level Designer" exclude; "Engine Programmer", "Graphics Programmer", "Tools Programmer", "Gameplay Engineer" include.

4. LEVEL: Seniority of this role. Title keyword takes priority:
   "Intern"/"Co-op"/"New Grad"/"New Graduate" → intern or junior (use junior for New Grad)
   "Junior"/"Associate"/"Entry" → junior
   "Senior" → senior
   "Staff" → staff
   "Principal" → principal
   "Manager"/"Director"/"VP"/"Head of" → manager
   "Lead"/"Tech Lead"/"Technical Lead" in an IC engineering title → senior
   No title keyword → use years of experience from description:
   0-2 → junior, 2-5 → mid, 5-10 → senior, 10+ → staff
   Numeric level indicators: L3/E3 → junior, L4/E4 → mid, L5/E5 → senior, L6+/E6+ → staff. "Distinguished Engineer" or "Fellow" → principal.
   If no signal at all → unclear

   Important: "Member of Technical Staff" and "Member of the Technical Staff" are job title conventions at some companies, not seniority indicators — ignore "Staff" in that phrase. Look for a seniority keyword elsewhere in the title (e.g. "Member of Technical Staff, Senior" → senior) or fall back to experience years.

   Respond with exactly one of: intern / junior / mid / senior / staff / principal / manager / unclear

5. CONTRACT: Is this a contract, temporary, or fixed-term position rather than permanent full-time employment?
   yes = contract, contractor, freelance, fixed-term, temporary, limited-term engagement, W-2 contractor, corp-to-corp (C2C), 1099, secondment, limited-term appointment
   no = permanent full-time employment (default if not stated); part-time permanent; probationary/trial period (standard for all jobs)
   Ignore domain uses of "contract" (e.g. "smart contracts", "government contracts").
   Respond with exactly one of: yes / no

6. HYBRID: Does this role require some in-office days while also allowing some remote work?
   yes = the description requires in-office days alongside remote work — "X days in office per week/month", "hybrid", or equivalent phrasing. Description content takes priority over location labels: if the description requires in-office time, mark yes even if the posting or title says "Remote".
   yes examples: "3 days/week in office", "hybrid schedule", "must be available for weekly in-person team meetings", "in-person for first 90 days then remote", "occasional in-office presence required"
   no = fully remote (no in-office requirement stated in the description), fully on-site, or work arrangement not mentioned
   no examples: "flexible work arrangement", "we welcome remote applicants", "our team is split between office and remote" (no personal requirement stated), "relocation assistance available" (on-site, not hybrid)
   Respond with exactly one of: yes / no

7. COMP: Base salary range stated in the posting.
   Preserve the original currency symbol (e.g. "$120k-$160k", "£75k-£100k", "€80k-€110k").
   Abbreviate thousands as k (e.g. £75,000 → £75k). If stated in full dollars/pounds/etc, keep as-is.
   If multiple ranges by location, use the overall min to overall max.
   If only a single figure, use that. If not stated, write: n/a
   If stated as an hourly rate, convert to annual (hourly × 2,000): "$45/hr" → "$90k".

8. COMP_EXTRAS: Non-salary compensation worth calling out — equity or bonus only.
   Use exactly "equity" for any equity/stock/RSU/options, and exactly "bonus" for any bonus type.
   Do not include standard benefits (401k, health insurance, PTO). If none, write: n/a

9. REGION: Where must the candidate be located to take this job?
   us = role is based in the US, or remote with no geographic restriction, or explicitly open to US candidates
   canada = role is based in Canada or remote open to Canada (and possibly US); not available to US-only candidates
   international = requires presence or work authorization in a non-US, non-Canada country; or on-site outside North America
   unclear = cannot determine from the posting
   Use the <location> field as primary signal; use the description to confirm or override it.
   "Remote" alone with no country restriction → us.
   "Remote (Worldwide)" or "Remote (Global)" with no US exclusion → us.
   "Remote (US only)" or "United States" → us.
   "Remote (UK)" or "Remote - Europe" or "EMEA" or "APAC" → international.
   If the location contains any non-US/Canada city, country name, or regional identifier — even formatted unusually (e.g. "Israel - Office - Tel Aviv", "Bengaluru, India / Remote") — classify as international, unless the description explicitly states the role is open to US-based candidates.
   Exception: if the location lists a US or Canada city alongside international ones (e.g. "San Francisco, CA / London, UK") → us, since US candidates can apply.
   Country names and cities like Israel, India, UK, Germany, Tel Aviv, London, Berlin, Bangalore, Singapore → international.
   Respond with exactly one of: us / canada / international / unclear

10. LOCATION: Normalized display location. Use the description to confirm or correct the ATS location field.
   US on-site: "City, ST" using 2-letter state code (e.g. "Boulder, CO" · "New York, NY" · "San Francisco, CA")
   US remote: "Remote"
   International on-site: "City, Country" (e.g. "London, UK" · "Berlin, Germany" · "Toronto, Canada")
   Multiple locations: join with " / " (e.g. "New York, NY / Remote" · "San Francisco, CA / New York, NY")
   "Remote, US" or "United States - Remote" → Remote
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
<location>{location}</location>
</job>"""

CLASSIFY_VERSION = "2"  # bump to force re-classification of all jobs

_TITLE_SKIP_PATTERNS: tuple[str, ...] = tuple(
    json.loads(TITLE_SKIP_FILE.read_text()) if TITLE_SKIP_FILE.exists() else []
)


def title_is_skip(title: str) -> bool:
    t = title.lower()
    return any(p in t for p in _TITLE_SKIP_PATTERNS)


def content_hash(job: dict) -> str:
    key = f"v{CLASSIFY_VERSION}:{job['id']}:{job['title']}:{job.get('raw_text', '')[:200]}:{job.get('location', '')}"
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


def log_error(message: str) -> None:
    _log_error("classify_jobs", message, LOG_FILE)


def classify_with_llm(job: dict) -> dict:
    description = html.unescape(job.get("raw_text") or "").strip()
    location = job.get("location") or "Not specified"
    user_message = USER_TEMPLATE.format(
        title=job["title"],
        company=job["company"],
        description=description,
        location=location,
    )
    text = chat(SYSTEM_PROMPT, user_message, max_tokens=512, log_error=log_error)
    return parse_response(text)


def main():
    jobs = json.loads(JOBS_FILE.read_text())

    existing: dict[str, dict] = {}
    if OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text())

    classify_all = "--all" in sys.argv
    today = datetime.now(timezone.utc).date().isoformat()

    def needs_work(j: dict) -> bool:
        if classify_all:
            return True
        if j["id"] in existing:
            return False  # already classified; use --all to reclassify
        return j.get("first_seen") == today

    pending = [j for j in jobs if (j.get("raw_text") or "").strip() and needs_work(j)]
    title_skipped = [j for j in pending if title_is_skip(j["title"])]
    with_desc = [j for j in pending if not title_is_skip(j["title"])]
    without_desc = sum(1 for j in jobs if not (j.get("raw_text") or "").strip())

    for job in title_skipped:
        existing[job["id"]] = {
            "is_engineering": False,
            "is_contract": False,
            "is_hybrid": False,
            "region": "unclear",
            "location": None,
            "job_summary": None,
            "skills": [],
            "level": None,
            "comp": None,
            "comp_extras": [],
            "source_hash": content_hash(job),
        }
        print(f"  [title-skip] {job['company']}: {job['title']}")

    with_desc.sort(key=lambda j: 0 if j.get("first_seen") == today else 1)

    print(f"\nBackend: {BACKEND} ({'Claude ' + CLAUDE_MODEL if BACKEND == 'claude' else 'Ollama ' + OLLAMA_MODEL})")
    print(f"{len(with_desc)} jobs to classify, {len(title_skipped)} title-skipped, {without_desc} skipped (no description)")
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

    usage = get_usage()
    stats = {
        "classified": len(with_desc),
        "title_skipped": len(title_skipped),
        "skipped_no_desc": without_desc,
        "errors": errors,
        **usage,
    }
    (Path(__file__).parent.parent / "data" / "jobs_pipeline_stats.json").write_text(json.dumps(stats, indent=2))

    if usage["requests"]:
        cost = estimate_cost(usage)
        print(f"Tokens — input: {usage['input_tokens']:,}  cache_read: {usage['cache_read_input_tokens']:,}  output: {usage['output_tokens']:,}  est. cost: ${cost:.3f}")


if __name__ == "__main__":
    main()
