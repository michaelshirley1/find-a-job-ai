"""
Daily job search, driven by a candidate profile (see scripts/profile.example.json).

Single entry point — scrapes Indeed/LinkedIn (via JobSpy) for whatever job title(s)
and location(s) the profile lists, runs a stack/language/work-authorization/
seniority filtering pipeline, grades every surviving listing A-F, and reports only
jobs not seen on a previous run.

Usage:
    cp scripts/profile.example.json scripts/profile.json   # then edit it
    python scripts/daily_job_search.py
    python scripts/daily_job_search.py --hours-old 48 --show-all
    python scripts/daily_job_search.py --profile scripts/other_profile.json

State lives in data/seen_jobs.json (job_url -> first_seen/last_seen/grade).
Reports are written to data/reports/YYYY-MM-DD.md.

MARKET-SPECIFIC EXTRAS: setting profile["market"] to "japan" additionally scrapes
three Japan-focused boards JobSpy doesn't cover (TokyoDev, Japan Dev, Daijob) and
turns on a Japanese-language requirement filter (JLPT-aware). Any other market value
runs JobSpy only, with no language-requirement filtering (nothing in this script
knows how to parse requirement phrasing for languages other than Japanese yet).

CAREERCROSS IS SKIPPED even under market=japan: as of 2026-07, its listing page
returns a Cloudflare JS-challenge (403) on a plain fetch and WebFetch-based
workarounds don't respect the search/query params. Re-enable only after confirming
a working fetch path.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, "seen_jobs.json")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
DEFAULT_PROFILE_PATH = os.path.join(REPO_ROOT, "scripts", "profile.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Candidate profile
# ---------------------------------------------------------------------------

def load_profile(path):
    if not os.path.exists(path):
        example = os.path.join(REPO_ROOT, "scripts", "profile.example.json")
        sys.exit(
            f"No profile found at {path}.\n"
            f"Copy {example} to {path} and fill it in "
            f"(the cv-parsing skill can generate it from a resume), then re-run."
        )
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)

    profile.setdefault("candidate_name", "Candidate")
    profile.setdefault("job_titles", ["Software Engineer"])
    profile.setdefault("locations", [])
    profile.setdefault("market", "other")
    profile.setdefault("country_indeed", "USA")
    profile.setdefault("years_experience", 3)
    profile.setdefault("core_skills", {})
    profile.setdefault("secondary_skills", {})
    profile.setdefault("core_stack_max", 40)
    profile.setdefault("secondary_stack_cap", 16)
    profile.setdefault("language_requirements", {})
    profile.setdefault("work_authorization", {})
    profile.setdefault("work_location_preference", "any")
    return profile


def build_skill_pattern(name):
    """Word-bounded regex for plain alphanumeric skill names; bare (unbounded)
    regex for names with symbols (c#, .net, c++, node.js) since \\b doesn't
    reliably straddle a non-word character at the edge of the token."""
    if re.match(r"^[a-z0-9]+$", name.lower()):
        return r"\b" + re.escape(name.lower()) + r"\b"
    return re.escape(name.lower())


def build_stack_patterns(skills):
    """skills: {name: weight} or {name: {"pattern": regex, "weight": weight}}."""
    patterns = {}
    for name, spec in skills.items():
        if isinstance(spec, dict):
            patterns[name] = (spec["pattern"], spec["weight"])
        else:
            patterns[name] = (build_skill_pattern(name), spec)
    return patterns


# ---------------------------------------------------------------------------
# Language requirement checks (Japanese module — the only one implemented)
# ---------------------------------------------------------------------------

JLPT_RANK = {"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5}  # lower = more proficient

JP_HARD_FAIL_PATTERNS = [
    r"native\s+japanese",
    r"business[- ]level\s+japanese",
    r"fluent\s+(in\s+)?japanese",
    r"japanese\s+and\s+english",
    r"japanese.{0,15}(required|mandatory|essential)",
    r"japanese\s+level",
    r"(communication|language)\s+skills?\s+in\s+japanese",
    r"japanese\s+to\s+(work|communicate|collaborate|interact)\s+(effectively\s+)?with",
    r"日本語で業務上の会話",
    r"日本語ビジネスレベル",
    r"日本語ネイティブレベル",
    r"daily conversation level",
]
JP_LEVEL_PATTERNS = [
    r"jlpt\s*n\s*([12345])",
    r"japanese\s*\(?n([12345])\+?\)?",
]
JP_POSITIVE_OVERRIDE = [
    r"no japanese required",
    r"japanese\s*[:\-]?\s*not required",
    r"japanese.{0,20}(nice to have|a plus|optional|ideally|not required)",
    r"japanese[:\s]+none\b",
]
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def cjk_ratio(text):
    if not text:
        return 0.0
    total = len(text)
    cjk = len(CJK_RE.findall(text))
    return cjk / total if total else 0.0


def check_language_japanese(text, tag_value, lang_req):
    """Returns (status, evidence) where status in clear/flagged/fail/silent."""
    tag_clear = set(lang_req.get("tag_clear", ["No Japanese required", "None"]))
    tag_flag = set(lang_req.get("tag_flag", ["Basic Japanese", "Minimum Communication Level"]))
    certified = lang_req.get("certified_level")
    candidate_rank = JLPT_RANK.get(str(certified).lower().strip()) if certified else None

    if tag_value is not None:
        if tag_value in tag_clear:
            return "clear", f"tag: {tag_value}"
        if tag_value in tag_flag:
            return "flagged", f"tag: {tag_value} (self-assessed/uncertified claim)"
        if tag_value == "required":
            return "fail", "tag: Japanese Required"
        m = re.search(r"n([12345])", tag_value.lower())
        if m and candidate_rank is not None and candidate_rank <= int(m.group(1)):
            return "clear", f"tag: {tag_value} (cleared by held level {certified})"
        return "fail", f"tag: {tag_value}"

    if not text:
        return "silent", "no description text available"

    low = text.lower()
    if cjk_ratio(text) > 0.25:
        return "fail", "majority-Japanese posting (CJK ratio check)"

    for pat in JP_LEVEL_PATTERNS:
        m = re.search(pat, low)
        if m:
            required_rank = int(m.group(1))
            if candidate_rank is not None and candidate_rank <= required_rank:
                return "clear", f"matched '{m.group(0)}', cleared by held level {certified}"
            for override in JP_POSITIVE_OVERRIDE:
                om = re.search(override, low)
                if om:
                    return "clear", f"explicit override: '{om.group(0)}' clears '{m.group(0)}'"
            return "fail", f"matched: '{m.group(0)}'"

    for pat in JP_HARD_FAIL_PATTERNS:
        m = re.search(pat, low)
        if m:
            for override in JP_POSITIVE_OVERRIDE:
                om = re.search(override, low)
                if om:
                    return "clear", f"explicit override: '{om.group(0)}' clears '{m.group(0)}'"
            return "fail", f"matched: '{m.group(0)}'"

    for override in JP_POSITIVE_OVERRIDE:
        m = re.search(override, low)
        if m:
            return "clear", f"explicit: '{m.group(0)}'"

    return "silent", "no Japanese requirement mentioned either way"


def check_language(text, tag_value, lang_req):
    target = str(lang_req.get("target_language") or "").lower()
    if target == "japanese":
        return check_language_japanese(text, tag_value, lang_req)
    return "not_applicable", "no language-requirement filter configured for this market"


# ---------------------------------------------------------------------------
# Work authorization checks
# ---------------------------------------------------------------------------

# Country-agnostic — apply regardless of target market.
WORKAUTH_GENERIC_DISQUALIFY = [
    r"must (already )?have.{0,20}(right|authorization|authorisation) to work",
    r"existing work (visa|permit)",
    r"no (visa )?sponsorship",
    r"not able to sponsor",
    r"unable to sponsor",
    r"does not sponsor",
]
WORKAUTH_GENERIC_POSITIVE = [
    r"visa sponsorship (available|provided|offered|supported)",
    r"we sponsor",
    r"relocation (support|package|assistance)",
    r"relocation offered",
    r"overseas visa sponsorship supported",
    r"apply from abroad",
]


def build_workauth_patterns(work_auth):
    country = work_auth.get("target_country")
    adjective = work_auth.get("country_adjective") or (country.lower() if country else None)

    disqualify = list(WORKAUTH_GENERIC_DISQUALIFY)
    positive = list(WORKAUTH_GENERIC_POSITIVE)
    if country:
        c = re.escape(country.lower())
        disqualify += [
            rf"must be authorized to work in {c} without sponsorship",
            rf"permanent resident (of {c})? required",
            rf"must be (currently )?in {c}.{{0,20}}authorized to work",
            rf"residing in {c}|previously lived in {c}",
            rf"applicants must be residing in {c}",
            rf"prior work experience in {c}",
        ]
    if adjective:
        disqualify.append(rf"{re.escape(adjective)} citizen(ship)?")
    return disqualify, positive


def check_workauth(text, tag_value, disqualify_patterns, positive_patterns):
    if tag_value == "apply_abroad":
        return "clear", "tag: Apply from abroad / Overseas visa sponsorship supported"
    if tag_value == "japan_only":
        return "fail", "tag: Japan/residents only"

    if not text:
        return "silent", "no description text available"

    low = text.lower()
    for pat in disqualify_patterns:
        m = re.search(pat, low)
        if m:
            return "fail", f"matched: '{m.group(0)}'"
    for pat in positive_patterns:
        m = re.search(pat, low)
        if m:
            return "clear", f"matched: '{m.group(0)}'"
    return "silent", "no sponsorship/work-authorization statement found"


# ---------------------------------------------------------------------------
# Location-type checks (remote vs. onsite/hybrid) — profile-independent regexes,
# gated by profile.work_location_preference
# ---------------------------------------------------------------------------

# JobSpy's own is_remote flag is unreliable (confirmed on a NZ search returning
# ordinary onsite/hybrid listings under is_remote=True) — read the description text
# instead, same approach as the language/work-auth checks above.
LOCATION_REMOTE_SIGNALS = [
    r"fully remote", r"100%\s*remote", r"remote[- ]first", r"remote[- ]only",
    r"work from anywhere", r"this is a (fully )?remote (role|position)",
    r"remote position", r"fully distributed team", r"remote,?\s*no office",
]
LOCATION_HYBRID_SIGNALS = [
    r"\bhybrid\b",
    # Bounded to 1-4 days/week — 5 days/week in-office is full-time onsite, not
    # hybrid, and must fall through to LOCATION_ONSITE_ONLY_SIGNALS instead.
    r"\b[1-4]\s*days?\s*(a|per)\s*week\s*(in|at)\s*(the\s*)?office",
    r"in.office\s*[1-4]\s*days?",
]
LOCATION_ONSITE_ONLY_SIGNALS = [
    r"on[- ]site only", r"not a remote (position|role)", r"no remote work",
    r"must work on[- ]site", r"in.office\s*(position|role)\b",
    r"5\s*days?\s*(a|per)\s*week\s*in\s*(the\s*)?office",
    r"relocation to (our )?office required",
]


def check_location_type(title, text, tags, preference):
    """preference: 'onsite_hybrid' (Japan-style — exclude remote-anywhere-only
    listings), 'remote' (exclude onsite-only listings), or anything else (no-op)."""
    if preference not in ("onsite_hybrid", "remote"):
        return "not_applicable", "no location-type preference configured for this profile"

    combined = f"{title} {' '.join(tags)} {text or ''}".lower()
    if not combined.strip():
        return "silent", "no description text available"

    remote_m = next((m for p in LOCATION_REMOTE_SIGNALS if (m := re.search(p, combined))), None)
    hybrid_m = next((m for p in LOCATION_HYBRID_SIGNALS if (m := re.search(p, combined))), None)
    onsite_m = next((m for p in LOCATION_ONSITE_ONLY_SIGNALS if (m := re.search(p, combined))), None)

    if preference == "onsite_hybrid":
        if remote_m and not hybrid_m:
            return "fail", f"fully remote, no onsite/hybrid option mentioned: '{remote_m.group(0)}'"
        if hybrid_m:
            return "clear", f"hybrid: '{hybrid_m.group(0)}'"
        return "silent", "no explicit remote/hybrid language found (assuming onsite by default)"

    # preference == "remote"
    if onsite_m and not (remote_m or hybrid_m):
        return "fail", f"onsite-only, no remote option mentioned: '{onsite_m.group(0)}'"
    if remote_m:
        return "clear", f"remote: '{remote_m.group(0)}'"
    if hybrid_m:
        return "flagged", f"hybrid, not fully remote: '{hybrid_m.group(0)}'"
    return "silent", "no explicit remote/hybrid/onsite language found"


# ---------------------------------------------------------------------------
# Seniority / role-family / gig checks (market- and profile-independent)
# ---------------------------------------------------------------------------

SENIORITY_EXCLUDE_TITLE = re.compile(
    r"\b(new grad|entry.level|early career|internship|intern|junior)\b", re.I
)
SENIORITY_STRETCH = re.compile(
    r"\b(senior|lead|staff|principal|manager|director|head of)\b", re.I
)
# Matches explicit years-of-experience asks in whatever phrasing the JD uses, e.g.
# "10+ years in server-side backend engineering", "4+ years of professional
# experience", "5-7 Years Experience". A much harder signal than the title-keyword
# check and must win when present.
YEARS_REQUIRED = re.compile(
    r"(\d{1,2})\+?\s*(?:[-–—]\s*|to\s+)?(\d{1,2})?\+?\s*years?\s*"
    r"(?:of\s+)?(?:professional\s+|relevant\s+|industry\s+)?"
    r"(?:experience|in\s+\w+(?:[\s-]\w+){0,3}|engineering|development)",
    re.I,
)
ROLE_ADJACENT = re.compile(
    r"\b(qa|sdet|site reliability|sre|devops|platform engineer|data engineer|"
    r"machine learning|ml engineer|test automation|data scientist|data science)\b", re.I
)
ROLE_OTHER = re.compile(
    r"\b(product manager|sales|recruiter|research scientist|hardware engineer|"
    r"forward deployed|solutions engineer|head of seo|vfx artist|game artist|"
    r"visual director)\b", re.I
)
AI_GIG_PATTERNS = [
    r"train (the )?next-generation ai",
    r"no prior ai experience required",
    r"per-project pay",
]
RECRUITER_SPAM_PATTERNS = [
    r"line\s*/\s*telegram", r"line id[:\s]", r"📫|💌",
]

# JobSpy returns descriptions in markdown by default, which backslash-escapes
# markdown-special characters — critically "-" becomes "\-" (e.g. "business-level"
# comes back as "business\-level"). Any regex expecting a literal single "-" would
# silently fail to match the escaped text unless we unescape first.
MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!])")


def unescape_markdown(text):
    return MARKDOWN_ESCAPE_RE.sub(r"\1", text) if text else text


def check_seniority(title, text, your_years):
    if SENIORITY_EXCLUDE_TITLE.search(title):
        return "exclude", f"title indicates junior/entry/intern level: '{title}'"

    full = f"{title} {text or ''}"
    best_years = None
    best_match = None
    for m in YEARS_REQUIRED.finditer(full):
        nums = [int(g) for g in m.groups() if g]
        if not nums:
            continue
        years = max(nums)
        if best_years is None or years > best_years:
            best_years = years
            best_match = m.group(0)

    if best_years is not None:
        if best_years >= your_years + 3.5:
            return "big_stretch", f"explicit ask of {best_years}+ years vs your ~{your_years}: '{best_match}'"
        if best_years >= your_years + 1.5:
            return "stretch", f"explicit ask of {best_years} years vs your ~{your_years}: '{best_match}'"
        return "match", f"explicit ask of {best_years} years is within your ~{your_years}yr band: '{best_match}'"

    combined = f"{title} {text[:2000] if text else ''}"
    m = SENIORITY_STRETCH.search(combined)
    if m:
        return "stretch", f"title-only signal, no explicit years found: '{m.group(0)}'"
    return "match", "seniority looks like a reasonable fit"


def check_role_family(title, text, tags):
    combined = f"{title} {' '.join(tags)} {text[:2000] if text else ''}"
    m = ROLE_OTHER.search(combined)
    if m:
        return "other", f"different role family: '{m.group(0)}'"
    m = ROLE_ADJACENT.search(combined)
    if m:
        return "adjacent", f"adjacent role family, not core dev: '{m.group(0)}'"
    return "core", "core software engineering role"


def check_ai_gig(text):
    if not text:
        return False, None
    low = text.lower()
    for pat in AI_GIG_PATTERNS:
        m = re.search(pat, low)
        if m:
            return True, m.group(0)
    return False, None


def check_recruiter_spam(text):
    if not text:
        return False
    low = text.lower()
    return any(re.search(pat, low) for pat in RECRUITER_SPAM_PATTERNS)


# ---------------------------------------------------------------------------
# Stack scoring
# ---------------------------------------------------------------------------

def score_stack(title, text, tags, core_patterns, secondary_patterns, core_max, secondary_cap, secondary_names):
    """Weighted by MENTION FREQUENCY, not just presence/absence — a JD that says
    "python" nine times and "react" twice is clearly a Python role with one
    incidental React mention, and frequency (not mere presence) is what tells
    those two cases apart."""
    combined = f"{title} {' '.join(tags)} {text}"

    core_hits, core_freq_total, core_weighted = {}, 0, 0
    for name, (pat, weight) in core_patterns.items():
        n = len(re.findall(pat, combined, re.I))
        if n:
            core_hits[name] = n
            core_freq_total += n
            core_weighted += weight * min(n, 3)  # diminishing returns per repeat mention

    secondary_hits, secondary_freq_total, secondary_weighted = {}, 0, 0
    for name, (pat, weight) in secondary_patterns.items():
        n = len(re.findall(pat, combined, re.I))
        if n:
            secondary_hits[name] = n
            secondary_freq_total += n
            secondary_weighted += weight * min(n, 3)

    core_weighted = min(core_weighted, core_max)
    secondary_weighted = min(secondary_weighted, secondary_cap)

    core_str = ", ".join(f"{k}×{v}" for k, v in core_hits.items()) or "none"
    secondary_str = ", ".join(f"{k}×{v}" for k, v in secondary_hits.items())

    if secondary_freq_total > 0 and secondary_freq_total >= core_freq_total:
        # secondary-only skill terms dominate the JD — cap hard regardless of
        # incidental core-stack mentions (adjacent team, nice-to-have, etc).
        total = min(secondary_cap - 1, 5 + core_weighted * 0.3)
        names = ", ".join(secondary_names) or "your secondary skills"
        note = (
            f"secondary/non-production skills only ({names}) — flag before applying — "
            f"dominant mentions: {secondary_str} vs core: {core_str}"
        )
    elif secondary_hits:
        total = core_weighted + secondary_weighted * 0.25
        note = f"core stack hits: {core_str} (plus secondary: {secondary_str})"
    else:
        total = core_weighted
        note = f"core stack hits: {core_str}"

    return round(min(total, core_max)), note


# ---------------------------------------------------------------------------
# Salary extraction
# ---------------------------------------------------------------------------

# Generic salary-looking substring for sources with no structured field
# (TokyoDev/Japan Dev card tags) — a currency symbol followed by a number,
# optionally a range, optionally K/M shorthand.
SALARY_TEXT_RE = re.compile(
    r"[¥$€£]\s?[\d][\d,\.]*\s?[MK]?(?:\s?[-–]\s?[¥$€£]?\s?[\d][\d,\.]*\s?[MK]?)?"
)


def format_salary_jobspy(r):
    """Pull JobSpy's structured min_amount/max_amount/currency/interval columns
    into one display string. Cells are NaN (not None) when absent — pandas'
    numeric-column fill — so isna() is required, a plain `is None` check misses
    them."""
    import pandas as pd

    mn, mx = r.get("min_amount"), r.get("max_amount")
    mn = None if pd.isna(mn) else mn
    mx = None if pd.isna(mx) else mx
    if mn is None and mx is None:
        return None

    currency = r.get("currency")
    currency = None if pd.isna(currency) else str(currency)
    interval = r.get("interval")
    interval = None if pd.isna(interval) else str(interval)

    def fmt(n):
        return f"{n:,.0f}"

    amount = f"{fmt(mn)}-{fmt(mx)}" if (mn is not None and mx is not None and mn != mx) else fmt(mn if mn is not None else mx)
    out = " ".join(p for p in [currency, amount] if p)
    return f"{out}/{interval}" if interval else out


def extract_salary_text(text):
    if not text:
        return None
    m = SALARY_TEXT_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_jobspy(job_titles, locations, country_indeed, hours_old):
    from jobspy import scrape_jobs
    import pandas as pd

    frames = []
    for term in job_titles:
        for loc in locations:
            try:
                df = scrape_jobs(
                    site_name=["indeed", "linkedin"],
                    search_term=term,
                    location=loc,
                    results_wanted=25,
                    country_indeed=country_indeed,
                    linkedin_fetch_description=True,
                    hours_old=hours_old,
                )
                frames.append(df)
            except Exception as e:
                print(f"  [jobspy] {term} / {loc}: ERROR {e}", file=sys.stderr)

    if not frames:
        return []

    all_df = pd.concat(frames, ignore_index=True)
    all_df.drop_duplicates(subset=["job_url"], inplace=True)

    jobs = []
    for _, r in all_df.iterrows():
        jobs.append({
            "source": f"jobspy:{r.get('site')}",
            "title": str(r.get("title") or ""),
            "company": str(r.get("company") or ""),
            "location": str(r.get("location") or ""),
            "job_url": str(r.get("job_url") or ""),
            "description": unescape_markdown(str(r.get("description") or "")),
            "tags": [],
            "language_tag": None,
            "sponsorship_tag": None,
            "detail_blocked": False,
            "salary": format_salary_jobspy(r),
        })
    return jobs


def scrape_tokyodev():
    r = requests.get("https://www.tokyodev.com/jobs", headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    anchors = soup.select("a[href^='/companies/'][href*='/jobs/']")
    seen = {}
    lang_vals = {"Basic Japanese", "Business Japanese", "Conversational Japanese", "Fluent Japanese", "No Japanese required"}
    for a in anchors:
        href = a["href"]
        if href in seen:
            continue
        # Exactly 2 parent levels — validated against the live DOM. Walking further
        # up (previously 4) lands on the per-COMPANY container that can bundle
        # several distinct job cards together, so every job under one company
        # silently inherited the same (first) title and the same blended tag/text
        # for stack scoring. 2 levels is the tightest per-job boundary.
        card = a.parent.parent if a.parent is not None else None
        text = card.get_text(" | ", strip=True) if card else a.get_text(" | ", strip=True)
        parts = [p.strip() for p in text.split(" | ")]
        lang = next((p for p in parts if p in lang_vals), None)
        apply_abroad = "Apply from abroad" in parts
        japan_only = "Japan residents only" in parts
        seen[href] = {
            "source": "tokyodev",
            "title": parts[0] if parts else "",
            "company": href.split("/")[2] if len(href.split("/")) > 2 else "",
            "location": "Tokyo/Japan",
            "job_url": "https://www.tokyodev.com" + href,
            "description": "",
            "tags": parts,
            "language_tag": lang,
            "sponsorship_tag": "apply_abroad" if apply_abroad else ("japan_only" if japan_only else None),
            "detail_blocked": True,
            "salary": extract_salary_text(text),
        }
    return list(seen.values())


def scrape_japandev():
    r = requests.get("https://japan-dev.com/jobs", headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("li.job-item")
    jobs = []
    for c in cards:
        a = c.find("a", href=True)
        href = a["href"] if a else None
        if not href:
            continue
        text = c.get_text(" | ", strip=True)
        parts = [p.strip() for p in text.split(" | ")]
        japanese_required = "Japanese Required" in parts
        residents_only = any("Residents Only" in p for p in parts)
        apply_abroad = "Apply from Abroad" in parts
        jobs.append({
            "source": "japandev",
            "title": parts[2] if len(parts) > 2 else (parts[0] if parts else ""),
            "company": parts[3] if len(parts) > 3 else "",
            "location": "Tokyo/Japan",
            "job_url": "https://japan-dev.com" + href,
            "description": "",
            "tags": parts,
            "language_tag": "required" if japanese_required else None,
            "sponsorship_tag": "japan_only" if residents_only else ("apply_abroad" if apply_abroad else None),
            "detail_blocked": False,
            "_href": href,
            "salary": extract_salary_text(text),
        })
    return jobs


def fetch_japandev_detail(href):
    url = "https://japan-dev.com" + href
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""


def scrape_daijob(job_titles):
    keep_levels = {"None", "Minimum Communication Level"}
    terms = [re.sub(r"\s+", "+", t.strip()) for t in job_titles]
    cards = {}
    for term in terms:
        url = f"https://www.daijob.com/en/jobs/search_result?keywords={term}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            for c in soup.select("article.job-card"):
                link_tag = c.find("a", href=re.compile(r"/en/jobs/detail/\d+"))
                if not link_tag:
                    continue
                href = link_tag["href"]
                if href in cards:
                    continue
                text = c.get_text(" | ", strip=True)
                m = re.search(r"Japanese Level \| ([^|]+)", text)
                lvl = m.group(1).strip() if m else "UNKNOWN"
                if lvl not in keep_levels:
                    continue
                title_m = re.search(r"Staff Level \| ([^|]+) \| ([^|]+)", text)
                sal_m = re.search(r"Salary \| ([^|]+)", text)
                cards[href] = {
                    "source": "daijob",
                    "title": title_m.group(2).strip() if title_m else text[:80],
                    "company": title_m.group(1).strip() if title_m else "unknown",
                    "location": "Tokyo/Japan",
                    "job_url": "https://www.daijob.com" + href,
                    "description": text,
                    "tags": [f"Japanese Level: {lvl}"],
                    "language_tag": lvl,
                    "sponsorship_tag": None,
                    "detail_blocked": False,
                    "_href": href,
                    "salary": sal_m.group(1).strip() if sal_m else extract_salary_text(text),
                }
        except Exception as e:
            print(f"  [daijob] {term}: ERROR {e}", file=sys.stderr)
    return list(cards.values())


def fetch_daijob_detail(href):
    url = "https://www.daijob.com" + href
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

GRADE_BANDS = [(85, "A"), (70, "B"), (55, "C"), (40, "D"), (0, "F")]


def grade_from_score(score):
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def estimate_interview_chance(score, lang_status, auth_status, sen_status, role_status, recruiter_spam, loc_status):
    """Heuristic estimate, NOT a statistical model — there's no historical outcome
    data behind this. It's a calibrated-by-feel translation of the same signals
    already in the grade, biased low. Use it to rank A/B listings against each
    other, not as a real probability."""
    base = max(0.0, min(70.0, (score - 40) * 1.1))

    if lang_status in ("clear", "not_applicable") and auth_status == "clear":
        base += 5
    if lang_status in ("flagged", "silent"):
        base -= 6
    if auth_status in ("flagged", "silent"):
        base -= 8
    if sen_status == "stretch":
        base -= 6
    elif sen_status == "big_stretch":
        base -= 22
    if role_status == "adjacent":
        base -= 5
    elif role_status == "other":
        base -= 10
    if recruiter_spam:
        base -= 10
    if loc_status == "silent":
        # A configured onsite_hybrid/remote preference with no explicit signal in the
        # JD is unverified, not cleared — same treatment as a silent language/work-auth
        # check. loc_status is "not_applicable" (no penalty) when no preference is set.
        base -= 7

    return round(max(3.0, min(70.0, base)))


def evaluate(job, ctx):
    """Mutates job in place with evaluation fields. Returns True if it should be graded, False if hard-excluded."""
    title = job["title"]
    text = job["description"]
    tags = job.get("tags") or []
    job.setdefault("salary", None)

    ai_gig, ai_evidence = check_ai_gig(text)
    if ai_gig:
        job["excluded"] = True
        job["exclusion_reason"] = f"AI-training/RLHF gig, not a real role: '{ai_evidence}'"
        return False

    lang_status, lang_evidence = check_language(text, job.get("language_tag"), ctx["lang_req"])
    if lang_status == "fail":
        job["excluded"] = True
        job["exclusion_reason"] = f"Language requirement: {lang_evidence}"
        return False

    if ctx["workauth_is_citizen"]:
        # A candidate who already holds citizenship of the target country needs no
        # visa or sponsorship at all — none of the sponsorship-related disqualify
        # patterns (built for the "needs an employer to sponsor a visa" case) or a
        # bare "{country} citizenship required" phrase apply to them. Short-circuit
        # rather than pattern-match: previously a genuine NZ citizen was wrongly
        # excluded from an NZ-market search over a posting that said "New Zealand
        # citizenship" required — a requirement they trivially satisfy.
        auth_status, auth_evidence = "clear", f"candidate holds {ctx['workauth_country']} citizenship — no visa/sponsorship needed"
    else:
        auth_status, auth_evidence = check_workauth(
            text, job.get("sponsorship_tag"), ctx["workauth_disqualify"], ctx["workauth_positive"]
        )
    if auth_status == "fail":
        job["excluded"] = True
        job["exclusion_reason"] = f"Work authorization: {auth_evidence}"
        return False

    loc_status, loc_evidence = check_location_type(title, text, tags, ctx["location_pref"])
    if loc_status == "fail":
        job["excluded"] = True
        job["exclusion_reason"] = f"Location type: {loc_evidence}"
        return False

    sen_status, sen_evidence = check_seniority(title, text, ctx["years_experience"])
    if sen_status == "exclude":
        job["excluded"] = True
        job["exclusion_reason"] = sen_evidence
        return False

    role_status, role_evidence = check_role_family(title, text, tags)
    recruiter_spam = check_recruiter_spam(text)

    stack_score, stack_note = score_stack(
        title, text, tags,
        ctx["core_patterns"], ctx["secondary_patterns"],
        ctx["core_max"], ctx["secondary_cap"], ctx["secondary_names"],
    )

    lang_score = {"clear": 20, "flagged": 12, "silent": 15, "not_applicable": 20}[lang_status]
    auth_score = {"clear": 20, "flagged": 12, "silent": 8}[auth_status]
    sen_score = {"match": 10, "stretch": 6, "big_stretch": 1}[sen_status]
    role_score = {"core": 10, "adjacent": 5, "other": 0}[role_status]

    total = stack_score + lang_score + auth_score + sen_score + role_score
    if recruiter_spam:
        total = min(total, 69)  # cap at C — unreliable / staffing-agency-template listing
    if sen_status == "big_stretch":
        total = min(total, 64)  # cap at C — an explicit large years-of-experience gap
    if loc_status == "silent":
        # Under a configured onsite_hybrid/remote preference, a JD that says nothing
        # about remote/hybrid/onsite is unverified, not cleared — knock it down a
        # rough grade-band's worth of points so it ranks below a confirmed match
        # instead of tying with one. No penalty when loc_status is "not_applicable"
        # (no preference configured on this profile).
        total = max(0, total - 15)

    grade = grade_from_score(total)
    interview_pct = estimate_interview_chance(
        total, lang_status, auth_status, sen_status, role_status, recruiter_spam, loc_status
    )

    job.update({
        "excluded": False,
        "score": total,
        "grade": grade,
        "interview_pct": interview_pct,
        "language_status": lang_status,
        "language_evidence": lang_evidence,
        "workauth_status": auth_status,
        "workauth_evidence": auth_evidence,
        "seniority_status": sen_status,
        "seniority_evidence": sen_evidence,
        "role_status": role_status,
        "role_evidence": role_evidence,
        "location_status": loc_status,
        "location_evidence": loc_evidence,
        "stack_score": stack_score,
        "stack_note": stack_note,
        "recruiter_spam": recruiter_spam,
    })
    return True


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(new_graded, new_excluded, today, all_graded_count, all_excluded_count, profile):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    # Suffix by market so running multiple profiles on the same day doesn't
    # clobber each other's report (bare "japan" keeps the pre-existing filename
    # convention for the original/default profile).
    market = profile.get("market") or "other"
    suffix = "" if market == "japan" else f"-{market}"
    path = os.path.join(REPORTS_DIR, f"{today}{suffix}.md")

    new_graded.sort(key=lambda j: (-j["score"], j["title"]))

    lines = [f"# Job search report — {today} ({profile['candidate_name']})", ""]
    lines.append(
        f"Scraped and filtered {all_graded_count + all_excluded_count} listings total "
        f"({all_graded_count} graded, {all_excluded_count} excluded). "
        f"{len(new_graded)} graded listing(s) and {len(new_excluded)} excluded listing(s) are new since the last run."
    )
    lines.append("")
    lines.append(
        "**Grading rubric (0-100):** stack fit (0-40, secondary/non-production skills capped low), "
        "language clearance (0-20), work-authorization clearance (0-20), seniority fit (0-10), "
        f"role-family fit (0-10). A ≥85, B ≥70, C ≥55, D ≥40, F <40. Recruiter/staffing-template "
        f"listings, and any listing with a years-of-experience ask well above your ~{profile['years_experience']}, "
        "are capped at C regardless of score. Location-type mismatches (fully-remote-only listings under an "
        "onsite/hybrid profile, or onsite-only listings under a remote profile) are hard-excluded, not scored — "
        "see 'Location' column / excluded-listings section. A profile with a location preference configured "
        "(onsite_hybrid/remote) also docks 15 points from any listing that's silent on remote/hybrid/onsite "
        "(unverified, not cleared) — it stays in the results but ranks below a confirmed match."
    )
    lines.append("")
    lines.append(
        "**Est. interview chance** is a heuristic derived from the same signals as the grade — "
        "it is NOT based on any historical outcome data, just a calibrated-low ranking aid. "
        "Use it to sort A/B listings against each other, not as a real probability."
    )
    lines.append("")

    a_b = [j for j in new_graded if j["grade"] in ("A", "B")]
    rest = [j for j in new_graded if j["grade"] not in ("A", "B")]

    if a_b:
        lines.append("## New A/B listings — ranked by estimated interview chance")
        lines.append("")
        lines.append("| Grade | Est. interview % | Score | Company | Role | Salary | Stack note | Language | Work-auth | Location | Seniority | Link |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for j in sorted(a_b, key=lambda j: (-j["interview_pct"], -j["score"])):
            lines.append(
                f"| {j['grade']} | {j['interview_pct']}% | {j['score']} | {j['company']} | {j['title']} | "
                f"{j.get('salary') or '—'} | "
                f"{j['stack_note']} | {j['language_status']}: {j['language_evidence']} | "
                f"{j['workauth_status']}: {j['workauth_evidence']} | "
                f"{j['location_status']}: {j['location_evidence']} | "
                f"{j['seniority_status']}: {j['seniority_evidence']} | [{j['source']}]({j['job_url']}) |"
            )
        lines.append("")

    if rest:
        lines.append("## New C/D/F listings")
        lines.append("")
        lines.append("| Grade | Score | Company | Role | Salary | Stack note | Language | Work-auth | Location | Seniority | Link |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for j in rest:
            lines.append(
                f"| {j['grade']} | {j['score']} | {j['company']} | {j['title']} | "
                f"{j.get('salary') or '—'} | "
                f"{j['stack_note']} | {j['language_status']}: {j['language_evidence']} | "
                f"{j['workauth_status']}: {j['workauth_evidence']} | "
                f"{j['location_status']}: {j['location_evidence']} | "
                f"{j['seniority_status']}: {j['seniority_evidence']} | [{j['source']}]({j['job_url']}) |"
            )
        lines.append("")

    if not new_graded:
        lines.append("## New graded listings")
        lines.append("")
        lines.append("None today.")
        lines.append("")

    if new_excluded:
        lines.append("## New excluded listings (why)")
        lines.append("")
        for j in new_excluded:
            sal = f" ({j['salary']})" if j.get("salary") else ""
            lines.append(f"- **{j['company']}** — {j['title']}{sal}: {j['exclusion_reason']} ([{j['source']}]({j['job_url']}))")
        lines.append("")

    lines.append("## Source notes")
    lines.append("")
    source_notes = ["- JobSpy (Indeed/LinkedIn): last-24h window (or as configured via --hours-old)."]
    if profile["market"] == "japan":
        source_notes += [
            "- Japan Dev: 'NEW!' badge used as a loose recency proxy, not exact.",
            "- TokyoDev: no recency field — general sweep, tag-only (detail pages Cloudflare-blocked).",
            "- Daijob: no recency filter applied — general sweep.",
            "- CareerCross: skipped (listing page 403s under Cloudflare as of 2026-07).",
        ]
    lines.append("\n".join(source_notes))
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=DEFAULT_PROFILE_PATH, help="Path to candidate profile JSON (default: scripts/profile.json)")
    parser.add_argument("--hours-old", type=int, default=24, help="JobSpy recency window in hours (default 24)")
    parser.add_argument("--show-all", action="store_true", help="Report all graded/excluded jobs, not just new ones")
    parser.add_argument("--skip-jobspy", action="store_true")
    parser.add_argument("--skip-tokyodev", action="store_true")
    parser.add_argument("--skip-japandev", action="store_true")
    parser.add_argument("--skip-daijob", action="store_true")
    args = parser.parse_args()

    profile = load_profile(args.profile)

    lang_req = profile["language_requirements"]
    workauth_disqualify, workauth_positive = build_workauth_patterns(profile["work_authorization"])
    ctx = {
        "lang_req": lang_req,
        "workauth_disqualify": workauth_disqualify,
        "workauth_positive": workauth_positive,
        "workauth_is_citizen": bool(profile["work_authorization"].get("is_citizen")),
        "workauth_country": profile["work_authorization"].get("target_country") or "the target country",
        "years_experience": profile["years_experience"],
        "core_patterns": build_stack_patterns(profile["core_skills"]),
        "secondary_patterns": build_stack_patterns(profile["secondary_skills"]),
        "core_max": profile["core_stack_max"],
        "secondary_cap": profile["secondary_stack_cap"],
        "secondary_names": list(profile["secondary_skills"].keys()),
        "location_pref": profile["work_location_preference"],
    }

    today = datetime.now().strftime("%Y-%m-%d")  # local date — this is a once-a-day-by-local-calendar tool
    state = load_state()
    is_japan = profile["market"] == "japan"

    all_jobs = []

    if not args.skip_jobspy:
        print("Scraping JobSpy (Indeed/LinkedIn)...")
        all_jobs.extend(scrape_jobspy(profile["job_titles"], profile["locations"], profile["country_indeed"], args.hours_old))

    if is_japan and not args.skip_tokyodev:
        print("Scraping TokyoDev...")
        try:
            all_jobs.extend(scrape_tokyodev())
        except Exception as e:
            print(f"  TokyoDev scrape failed: {e}", file=sys.stderr)

    if is_japan and not args.skip_japandev:
        print("Scraping Japan Dev...")
        try:
            jd_cards = scrape_japandev()
            # Fetch details for every card that isn't already hard-excluded by its
            # tag (Japanese Required / Residents Only). Do NOT additionally filter
            # by a stack keyword in the card tags first — some cards carry no tech
            # tag at all despite having a hidden requirement on the detail page.
            # Skipping the detail fetch on a tag-based stack guess is exactly how
            # that kind of hidden requirement slips through.
            candidates = [
                j for j in jd_cards
                if j["language_tag"] != "required"
                and j["sponsorship_tag"] != "japan_only"
            ]
            print(f"  {len(jd_cards)} cards, {len(candidates)} worth a detail fetch")
            for j in candidates:
                j["description"] = fetch_japandev_detail(j["_href"])
                time.sleep(0.8)
                low = j["description"].lower()
                if "japanese: not required" in low:
                    j["language_tag"] = "No Japanese required"
                elif re.search(r"japanese:\s*(fluent|business|conversational|native)", low):
                    m = re.search(r"japanese:\s*(\w+)", low)
                    j["language_tag"] = m.group(1).title() + " Japanese" if m else None
                if "overseas visa sponsorship supported" in low or "visa sponsorship" in low:
                    j["sponsorship_tag"] = "apply_abroad"
                all_jobs.append(j)
        except Exception as e:
            print(f"  Japan Dev scrape failed: {e}", file=sys.stderr)

    if is_japan and not args.skip_daijob:
        print("Scraping Daijob...")
        try:
            dj_cards = scrape_daijob(profile["job_titles"])
            print(f"  {len(dj_cards)} cards pass the Japanese-level pre-filter")
            for j in dj_cards:
                detail = fetch_daijob_detail(j["_href"])
                time.sleep(0.8)
                if detail:
                    j["description"] = detail
                all_jobs.append(j)
        except Exception as e:
            print(f"  Daijob scrape failed: {e}", file=sys.stderr)

    # dedupe
    seen_urls = set()
    deduped = []
    for j in all_jobs:
        if j["job_url"] in seen_urls:
            continue
        seen_urls.add(j["job_url"])
        deduped.append(j)

    print(f"\n{len(deduped)} unique listings after dedupe. Evaluating...")

    graded, excluded = [], []
    for j in deduped:
        ok = evaluate(j, ctx)
        (graded if ok else excluded).append(j)

    new_graded, new_excluded = [], []
    for j in graded + excluded:
        url = j["job_url"]
        is_new = url not in state
        if is_new:
            (new_graded if not j["excluded"] else new_excluded).append(j)
            state[url] = {
                "first_seen": today, "last_seen": today,
                "title": j["title"], "company": j["company"],
                "grade": j.get("grade"), "excluded": j["excluded"],
            }
        else:
            state[url]["last_seen"] = today

    save_state(state)

    report_graded = graded if args.show_all else new_graded
    report_excluded = excluded if args.show_all else new_excluded
    path = write_report(report_graded, report_excluded, today, len(graded), len(excluded), profile)

    print(f"\nDone. {len(graded)} graded ({len(new_graded)} new), {len(excluded)} excluded ({len(new_excluded)} new).")
    print(f"Report: {path}")
    print(f"State: {STATE_PATH}")


if __name__ == "__main__":
    main()
