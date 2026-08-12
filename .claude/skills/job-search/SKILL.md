---
name: job-search
description: This skill should be used when the user asks to "search for jobs", "find me a job", "look for job listings", "run a job search", or otherwise wants job listings sourced via JobSpy (Indeed/LinkedIn) for whatever job title(s), location(s), and candidate background they're searching with. If the target market is Japan, also pulls in TokyoDev, Japan Dev, CareerCross, and Daijob. Driven entirely by a candidate profile (see scripts/profile.example.json and the cv-parsing skill) rather than any one hardcoded person, and works for any job title or field — not just software engineering.
version: 2.0.0
---

# Job Search (JobSpy + optional Japan job boards)

Searches jobs via the `python-jobspy` package (`scripts/daily_job_search.py`) for
Indeed/LinkedIn, **plus**, when the candidate profile's `market` is `"japan"`,
direct scrapes of four Japan-focused boards JobSpy doesn't cover — TokyoDev, Japan
Dev, CareerCross, and Daijob (see "Japan-specific job boards" below) — and filters
everything down to listings genuinely worth the candidate's time.

This skill has no hardcoded candidate or job title baked in. Every run needs a
candidate profile to work from.

## Candidate profile

All filtering/scoring (stack fit, language requirement, work authorization,
seniority) is driven by a profile — see `scripts/profile.example.json` for the
full schema. It covers: target job title(s), target location(s), market,
years of experience, **core (production) skills** vs **secondary (hobby/
self-taught/non-production) skills**, a language-requirement block, and a
work-authorization block.

Before running a search:

1. Check whether `scripts/profile.json` already exists and looks current for
   this conversation. If a newer CV/resume or a different stated target
   (new job title, new city, updated skills) is supplied in the conversation,
   prefer that over whatever's on disk.
2. If it doesn't exist, or is stale: run the **cv-parsing** skill against the
   user's resume/CV to generate it, or — if no CV is available — ask the user
   directly for the fields above. Don't invent or assume a profile; a wrong
   profile silently mis-grades every result (e.g. crediting a hobby language as
   production experience, or missing a real visa/language disqualifier).
3. Only after the profile is in place, proceed with the search.

The **core vs. secondary skill** distinction matters a lot for scoring: core
skills are the candidate's real, production, get-paid-for-it experience;
secondary skills are real but hobby-only/self-taught/no-production-experience —
`scripts/daily_job_search.py` caps how far secondary skills alone can carry a
grade (a JD whose primary ask is a secondary-only skill gets flagged, not scored
as if it were a core match, even if the candidate has genuine personal-project
experience with it).

Similarly, for language requirements: a candidate's own self-assessed study level
(e.g. "roughly N4-level vocab") is **not** the same as holding a certificate, and
a JD asking for a *specific certified level* the candidate can't truthfully claim
is a real mismatch, not a pass. `profile.language_requirements.certified_level`
should only be set to a level the candidate can actually document. Same logic for
work authorization: "eligible to apply for a visa" is not "currently authorized to
work" — capture the real status in `profile.work_authorization.status`/`notes`
rather than rounding it up.

## Daily automated script

`scripts/daily_job_search.py` consolidates this entire pipeline — JobSpy
(Indeed/LinkedIn), plus the Japan board scrapers when `market` is `"japan"`, the
full language/work-authorization/seniority/stack filtering, and A-F grading of
every listing that survives — into a single reviewed, fixed script driven by
`scripts/profile.json`. It also tracks state in `data/seen_jobs.json` so re-runs
only report genuinely new listings, and writes a dated report to
`data/reports/YYYY-MM-DD.md`.

For "run today's search" / "what's new today" requests, prefer running this script
over redoing the scraping manually turn-by-turn — it's the same pipeline, already
debugged (including the per-site gotchas documented below), and it's the only way
results get deduped against prior days. Manual/ad-hoc scraping (documented below)
is for one-off requests that deviate from the profile's defaults (different search
terms, different cities, one specific company, a different market entirely) where
the script's profile-driven defaults don't fit.

```
cp scripts/profile.example.json scripts/profile.json   # first time only, then edit it
python scripts/daily_job_search.py                     # default daily run
python scripts/daily_job_search.py --hours-old 48       # widen the JobSpy recency window
python scripts/daily_job_search.py --show-all           # report everything, not just new-since-last-run
python scripts/daily_job_search.py --profile scripts/other_profile.json   # e.g. a second candidate/search
```

This exact command is allow-listed in `.claude/settings.json` so it doesn't need
re-approval every run — that's safe specifically because it's a fixed, reviewed
script with no `eval`/arbitrary-code surface, unlike ad-hoc `python -c` calls.
If the pipeline logic needs to change, edit the script (which then needs review
like any other code change) rather than reverting to inline scraping.

CV/resume generation for top hits is a planned future addition — not implemented
yet, do not build it unless explicitly asked.

## Setup

```
pip install -r scripts/requirements.txt   # or: make deps-jobspy
```

## Running the search — the critical gotcha

**Always pass `linkedin_fetch_description=True`.** Without it, every LinkedIn result
comes back with an *empty* description field, which makes language/requirement
filtering silently pass everything (a real listing requiring JLPT N2+ slipped
through the filter on the first pass this way — the Indeed copy of the same job had
the real description; the LinkedIn copy didn't).  Indeed descriptions come through
fine by default.

Call `scrape_jobs` directly for ad-hoc/manual searches:

```python
from jobspy import scrape_jobs
import json, pandas as pd

df = scrape_jobs(
    site_name=['indeed', 'linkedin'],
    search_term='<job title from the profile, or whatever the user asks for>',
    location='<a location from the profile, or whatever the user asks for>',
    results_wanted=20,
    country_indeed='<profile.country_indeed>',
    linkedin_fetch_description=True,
)
```

Run one call per (search term × location) combo, concat the DataFrames, dedupe on
`job_url`. `ZipRecruiter`/`Glassdoor` are US-focused — skip them for non-US
searches. `google` as a site tends to return little useful signal for most
markets — skip unless asked.

Pass `hours_old=24` when the user wants only fresh postings (e.g. "search the last
24 hours", "what's new today"). Works for both Indeed and LinkedIn. Default (no
`hours_old`) returns whatever's currently listed regardless of age — use that for
a general sweep, and only add the recency filter when asked, since it can shrink
results a lot on a slow day.

On Windows, avoid printing raw scraped text to the terminal — CJK-heavy or other
non-Latin-heavy descriptions will blow up `cp1252` encoding. Write results to
JSON/files instead (`PYTHONIOENCODING=utf-8` env var when you do print).

## Search terms

There's no fixed list of job titles — use whatever's in `profile.job_titles`, or
whatever the user asks for in the moment. This works for any job title/field
JobSpy can search (engineering, design, product, sales, ops, etc.) — the pipeline's
filtering logic (stack/skill scoring, language, work authorization, seniority,
role-family) is generic, not tied to software roles specifically, though the
built-in per-company gotchas below happen to be software-market examples since
that's what this skill has been used for so far.

## Japan-specific job boards (not covered by JobSpy)

Only relevant when the candidate's target market is Japan
(`profile.market == "japan"`) — skip this whole section for other markets.

JobSpy only wraps Indeed/LinkedIn/ZipRecruiter/etc — it has no scraper for these four
Japan-focused boards. Fetch each with plain HTTP GET (`requests`, `User-Agent:
Mozilla/5.0 ...` — no API key or auth needed) and fold the results into the *same*
dedupe/filter/tier pipeline as the JobSpy output, not a separate report. Normalize each
into the same shape JobSpy returns (`title`, `company`, `location`, `job_url`,
`description`, `site`) before concatenating.

Two of the four (TokyoDev, CareerCross) put a Cloudflare JS-challenge in front of
individual job *detail* pages — a plain GET returns a "Just a moment…" page, not the
posting. Their listing/search-results pages are **not** challenge-gated, though, and
carry enough structured data (language level, salary, sponsorship/remote tags) in the
card markup itself to filter on without ever opening the detail page. The other two
(Japan Dev, Daijob) have no such block — detail pages fetch fine and give you the full
description text.

### TokyoDev (tokyodev.com)
- `GET https://www.tokyodev.com/jobs` — plain fetch works, listing page is not
  bot-gated.
- Job links follow `/companies/{company-slug}/jobs/{job-slug}`.
- Each card's HTML carries the signal directly: a salary-range tag, a language tag
  (e.g. "Business Japanese" / "Conversational Japanese" — absent if nothing stated),
  an `apply-from-abroad` tag (relocation/sponsorship-friendly signal — treat as a
  positive), a `remote`/`no-remote`/`hybrid` tag, and tech/skill tags. Pull language
  and sponsorship signal from these tags rather than a description regex.
- **Detail pages are Cloudflare-protected.** A plain `requests.get()` on
  `/companies/.../jobs/...` returns the JS-challenge shell, not the real posting —
  don't try to regex a description out of it. If full text is genuinely needed, try
  the WebFetch tool (may still fail); otherwise work from the listing-card tags alone
  and say so in the output (don't call it "unverified" the way a truly silent JD would
  be — say "tag-only, detail page blocked").
- No posted-date field on the listing cards, so there's no reliable last-24h filter
  here — treat TokyoDev as a general-sweep source regardless of what recency window
  the user asked for elsewhere.

### Japan Dev (japan-dev.com)
- `GET https://japan-dev.com/jobs` — plain fetch works, fully server-rendered
  (Nuxt SSR), not bot-gated.
- Job links follow `/jobs/{company-slug}/{job-slug}`.
- Cards carry a `NEW!` badge — the only recency signal available. Treat "has the NEW!
  badge" as the closest available proxy for a 24h filter, not an exact one; don't
  claim it's equivalent to `hours_old=24`.
- Also on the card: salary range, an `Apply from Abroad` tag (sponsorship/relocation
  signal — positive), a remote-level tag (e.g. "Partial Remote"), location, and
  tech/skill tags.
- **Detail pages fetch fine with plain `requests.get()`** (no Cloudflare block) — pull
  the full description straight from `/jobs/{company}/{slug}` and run it through the
  normal language/work-authorization regex checks like any other source.
- Internally backed by an Algolia index (`Job_production`) with facets including
  `japanese_level_enum`, `english_level_enum`, `remote_level`, `seniority_level`,
  `candidate_location`, and `skill_names` — no confirmed public query-param API for
  it, so for now just fetch `/jobs` and filter from the rendered HTML tags. Worth
  re-checking in future if a documented Algolia search key shows up in their JS
  bundle, since that would let you filter server-side by JLPT level directly.

### CareerCross (careercross.com)
- **As of 2026-07-28, the listing/search-results page itself now returns a
  Cloudflare JS-challenge (`Just a moment...`, HTTP 403) on a plain
  `requests.get()`** — this contradicts the "not bot-gated" note that used to be
  here. Re-verify before trusting either behavior; sites change their bot-protection
  over time.
- The `&language_ja[]=-1&language_ja[]=1&language_ja[]=2` pre-filter param
  (Japanese level None/Basic/Daily Conversation) is still believed correct in
  principle, but couldn't be confirmed working this run.
- **WebFetch as a fallback did get past the 403**, but the results it returned
  (job cards) did not match the search keyword or language-filter params at all —
  e.g. querying "full stack engineer" returned a "Native English Instructor" and an
  "Industrial Machinery Overseas Sales" posting. Don't trust WebFetch output from
  this site as if it honored the query string; either the query isn't actually
  applied server-side when fetched this way, or WebFetch's cache/rendering path
  serves a generic/cached page. Treat CareerCross as unreliable via automation until
  someone confirms a fetch path that both bypasses Cloudflare AND actually respects
  the search params — for now, skip it and tell the user it was skipped rather than
  reporting fabricated/unfiltered results as if they were real matches.
- Detail pages (`/en/job/detail-{id}`) are also Cloudflare-protected, unchanged from
  before.

### Daijob (daijob.com)
- `GET https://www.daijob.com/en/jobs/search_result?keywords={term}` — plain fetch
  works. Note the bare `/en/jobs/search?kw=...` form just returns a tiny redirect
  stub pointing at `search_result` — request `search_result` directly (or follow
  redirects) rather than treating the redirect stub as an empty result.
- Richest listing-card data of the four: Location, Salary, and an explicit
  **Japanese Level** field right on the card (e.g. "Business Level (JLPT Level 2 or
  N2)"), plus a job-description snippet — use the Japanese Level field as a fast
  first-pass filter before even opening anything.
- **Detail pages fetch fine with plain `requests.get()`** (`/en/jobs/detail/{id}`, no
  Cloudflare block) — full descriptions are available directly for the normal
  filtering pipeline.

### Bundling them in

Run all four alongside the JobSpy Indeed/LinkedIn calls for whatever search
term/category the user asked for, normalize into JobSpy's shape, concat, dedupe by
`job_url`, then run the full filtering pipeline below over the combined set before
tiering — don't report these four as a separate section unless the user specifically
asks to compare sources. Because TokyoDev's and CareerCross's `description` field will
be thin-to-empty (Cloudflare blocks the detail page), lean on their listing-card tags
(language bucket, sponsorship tag, salary) for the language and work-authorization
checks instead of expecting a body-text regex to catch anything, and label the
evidence column accordingly (e.g. "TokyoDev tag: Business Japanese" rather than a
quoted sentence) rather than defaulting to "unverified" the way you would for a source
whose description is silent on the topic.

## Filtering pipeline (do this for every batch)

1. **Dedupe** by `job_url`.
2. **CJK ratio check** on title and full description (regex range
   `[぀-ヿ㐀-䶿一-鿿]`) — flags postings that are majority Japanese-language even
   when no explicit "Japanese required" phrase appears. Only relevant when the
   target market/language is Japanese.
3. **Explicit language-requirement regex** over title+description. For Japanese,
   e.g.: `native japanese`, `business[- ]level japanese`, `fluent (in )?japanese`,
   `jlpt\s*n[12345]`, `japanese and english` (as a joint requirement),
   `japanese.{0,15}(required|mandatory|essential)`. Compare any explicit JLPT-N
   requirement against `profile.language_requirements.certified_level` (not the
   candidate's self-assessed study level) — only a requirement at or below a level
   the candidate actually holds a certificate for should clear.
4. **Positive override signals** — `no japanese required`, `japanese not required`,
   `japanese.{0,20}(nice to have|a plus|optional|ideally)` — these should *save* a
   listing that would otherwise trip the regex above (language wanted but not
   required).
5. **Work authorization check — as important as the language check.** Search
   title+description for disqualifying patterns: `must (already )?have.{0,20}(right|
   authorization|authorisation) to work`, `existing work (visa|permit)`,
   `no (visa )?sponsorship`, `not able to sponsor`, `unable to sponsor`,
   `does not sponsor`, plus target-country-specific patterns built from
   `profile.work_authorization.target_country`/`country_adjective` (e.g.
   `must be authorized to work in {country} without sponsorship`,
   `{country_adjective} citizen(ship)?`, `permanent resident of {country} required`,
   `residing in {country}|previously lived in {country}`). Positive signals that
   clear it: `visa sponsorship (available|provided|offered)`, `we sponsor`,
   `relocation (support|package|assistance)`, `relocation offered`, explicit
   CoE/visa-services mentions. If a listing says nothing either way, treat it as
   **unverified, not cleared** — same treatment as the Rakuten example below — and
   flag it rather than silently including it.
6. **Read the actual description, not just metadata.** LinkedIn's location tag is
   not reliable — recruiter/staffing-agency reposts get tagged to the target city
   while the real job is somewhere else entirely. Always check the body text for a
   real location/reporting-office statement before including.
7. **Sanity-check it's a real job**, not gig work: watch for "train next-generation
   AI systems", "no prior AI experience required — your domain knowledge is what
   matters", "project-based", "per-project pay" — these are RLHF/AI-training
   contractor postings wearing a "Senior Software Engineer" title, not real
   full-time roles. Exclude them even if the stack/language match looks great.
8. **Seniority filter** — exclude `new grad`, `entry-level`, `early career`,
   `internship`, `junior`. Flag (don't silently drop) postings wanting notably more
   years than `profile.years_experience` (e.g. an explicit years-of-experience ask
   3+ years above the candidate's, or 4+ yrs in a specific narrow stack they're
   newer to).
9. **Stack/skill relevance** — match against `profile.core_skills`/
   `secondary_skills`; category-dependent (don't demand backend hits on a
   pure-frontend search, etc).

## Ad-hoc searches in a market different from the profile's default

When asked to search a market the current profile isn't set up for (e.g. the
profile targets Japan but the user asks "what about NZ?"), this is a one-off
deviation from the script's profile-driven pipeline — do it manually via
`scrape_jobs` (same pattern as the "Running the search" section above), and
**skip** whatever language/work-authorization checks are specific to the
profile's configured market (not relevant elsewhere). Still apply:
markdown-unescape, AI-training-gig exclusion (by both company name and
body-text pattern), seniority filter, role-family filter, recruiter-spam check,
and stack/skill scoring.

- **JobSpy's `is_remote=True` is unreliable** — confirmed 2026-08-01 on a New
  Zealand search: `location="New Zealand", is_remote=True` returned mostly
  ordinary on-site/hybrid listings (explicit office locations, "Hybrid"/"boost
  days in the office" language) alongside genuine remote ones. Don't trust the
  flag alone — check each candidate's description for actual remote/hybrid/
  on-site language before calling it a remote match.
- **"Twine" postings** read like freelance-marketplace gig listings (generic
  "long-term project," no identifiable single employer, "ongoing work"
  availability ask) rather than a real employer's full-time role — treat like
  the AI-training gig-mill pattern and exclude, even though it doesn't match
  the existing `AI_GIG_PATTERNS` regex (it's not AI-training work, just a
  different flavor of contract/gig work).
- **"$X/hr Remote" contract postings** (seen: "Crossing Hurdles" — "Frontend
  Developer | $50/hr Remote", "UI Developer | $50/hr Remote", both explicitly
  `Type: Contract`, `Commitment: 10-40 hrs/week`) — exclude regardless of stack
  match if the candidate/profile is looking for long-term employment rather than
  contract gig work.

## Known per-company gotchas (accumulate over time)

- **AXA Japan** ("Senior Full Stack Software Engineer JS/TS") — requires JLPT N2+.
  Confirmed disqualifying despite a great stack match.
- **Mico / TokyoDev listings** — requires "business-level proficiency in both
  Japanese and English." The phrasing doesn't match a naive `business.*japanese`
  regex (word order), so check for `japanese and english` too.
- **Rakuten** internal-team postings often don't state a language requirement in
  the scraped JD at all, but Rakuten's domestic teams frequently expect business
  Japanese in practice. Treat silence from Rakuten as "unverified," not "cleared" —
  tell the user to double check before applying.
- **Canonical** roles are sometimes scoped to "Based in EMEA timezones" even when
  the LinkedIn posting is tagged Tokyo/Kyoto. Read the body.
- **Final Aim, Inc.** — explicitly requires prior work experience in Japan, which in
  practice also implies existing work authorization — treat as failing both the
  work-authorization and location-history checks.
- **Agilent Technologies (Japan R&D roles)** — explicitly requires Japanese
  language ability for day-to-day work despite an otherwise good stack match.
- **"YO IT Consulting" Remote postings** (Node.js Developer, JS/TS Developer,
  Senior Software Engineer, all "Remote") — these are AI-training/RLHF contractor
  gigs, not real engineering jobs. Exclude on sight regardless of stack match.
- **DataAnnotation** (any title ending "- AI Trainer" — React/Node.js/Full Stack/QA/
  Backend/Frontend Developer, Senior/Staff/Principal Software Engineer, etc.) and
  **YO AI Labs** ("Senior Software Engineer - Remote") — same AI-training/RLHF
  contractor-mill pattern as YO IT Consulting above, confirmed in a 2026-07-30
  NZ-remote ad-hoc search where DataAnnotation alone accounted for ~40% of raw
  LinkedIn results across dozens of near-identical "<Role> - AI Trainer" postings.
  Exclude on sight by company name; don't rely on the AI_GIG_PATTERNS body-text
  regex alone to catch these — see the markdown-escaping gotcha below for why it
  can silently miss the "next-generation AI" phrase.
- **JobSpy markdown-escaping bug (found 2026-07-30):** `scrape_jobs` returns
  descriptions in markdown format by default, which backslash-escapes markdown
  special characters — critically `-` becomes `\-` (e.g. "business-level" comes
  back as `business\-level`, "next-generation" as `next\-generation`, "entry-level"
  as `entry\-level`). Any regex expecting a literal single `-` (e.g.
  `business[- ]level`, `entry.level`, `train next-generation ai`) silently fails to
  match the escaped text — this let confirmed AI-training-gig postings (literal
  `train next\-generation AI systems` in the JD) sail through ungraded in the NZ
  search before the fix. `daily_job_search.py` unescapes markdown via
  `unescape_markdown()` right after pulling each JobSpy description — any new
  ad-hoc script that calls `scrape_jobs` directly needs the same fix, don't assume
  the description text is regex-clean.
- **Forefront Infotech "Web Developer"** and **Ecorp trainings "Full Stack
  Developer"** — LinkedIn tags them Tokyo but the JD body says Surat/Chennai,
  India. Mistagged, not real Tokyo openings.
- **PeopleX Inc. Active Connector Group** (LinkedIn reposts, multiple roles) —
  states the requirement as `**Japanese Level:** Fluent (★★★★☆)` or
  `**Japanese Level:** Intermediate`, i.e. label-then-level, not `fluent japanese`
  word order. The naive `fluent (in )?japanese` regex doesn't catch this — search
  for `japanese level` as its own pattern too. Their Frontend Engineer
  (React/TypeScript) posting also adds "Currently residing in Japan, or previously
  lived in Japan with plans to relocate back" — a residency-history prerequisite
  that disqualifies independent of the language line; add
  `residing in japan|previously lived in japan` to the work-authorization
  disqualify list.
- **Kaigen "Senior TypeScript / JavaScript Engineer"** (LinkedIn) — states
  `**Note: Must be currently in Japan & authorized to work**` and
  `**Bilingual: Japanese (N2+) & Business English**`. Neither phrase matches the
  obvious regexes: the work-auth line doesn't contain "sponsorship" or "must
  already have the right to work," and the language line gives the level as
  `Japanese (N2+)` without the literal string "JLPT." Add
  `must be (currently )?in japan.{0,20}authorized to work` and a bare
  `japanese \(?n[12345]\+?\)?` pattern (JLPT number without the "JLPT" prefix) to
  catch these. The same JD also contains "mentor junior engineers," which false-
  triggers the `\bjunior\b` seniority-exclude regex — that hit is a red herring,
  not the real disqualifier; don't stop investigating a listing just because the
  junior-seniority regex fired, check whether it's a "junior on my team" mention
  vs. "we're hiring a junior."
- **Skillhouse Staffing Solutions K.K. "Back End Developer - Settlement system"**
  (LinkedIn) — opens with `*Applicants MUST be residing in Japan`. Same
  residency-prerequisite pattern as PeopleX above; add to the work-authorization
  disqualify regex.
- **amptalk "Sr. Software Engineer (AI Product)"** — TokyoDev's card tag says only
  "Basic Japanese" (and Japan Dev's Language Requirements line even says "Japanese:
  Conversational" up top), but the same Japan Dev detail page's "Must haves" list
  states "Japanese language proficiency at JLPT N4 level or above" — a certified
  level requirement, which disqualifies any candidate who holds no certificate at
  that level. Confirms the general lesson: **a board's coarse language tag/bucket
  can undersell the actual JD requirement** — when a listing clears on tag alone
  and looks promising, and the detail page is fetchable (Japan Dev, Daijob), pull
  it and check the actual requirements list before trusting the tag.
- **LinkedIn listings titled in Chinese** (e.g. "赴日软件开发工程师" — "go-to-Japan
  software dev engineer" — from `LYC（株）`) — the scraped description was a
  garbled recruiter template (phone numbers, LINE ID, no real JD prose). These read
  as Chinese-recruiter broker spam targeting Chinese-speaking candidates for Japan
  relocation, not a genuine single-employer posting. Exclude on sight — don't try
  to run the normal language/stack regex over templated recruiter spam, since the
  garbled text can dodge both the CJK-ratio check (low prose-to-symbol ratio) and
  the requirement regexes.

## Output format

Group results by category if more than one was searched. One markdown table per
group: Company | Role | Language evidence (quote the actual line, don't
paraphrase) | Sponsorship/work-authorization evidence (quote it, or say
"unverified" if the JD is silent) | Stack fit note (call out gaps, don't oversell)
| Link.

After the table(s), include a short "why these were excluded" section naming
specific companies/roles and the specific disqualifying line found — this is what
makes the filter trustworthy rather than a black box. Don't just say "X postings
were filtered out"; say *why*, with a quote or a one-line reason, the way the
gotchas list above does.

**Always include the job posting link — no exceptions.** This applies to every
listing named anywhere in the response, not just the ones in the "matches" table:
excluded/dropped postings, borderline/FYI mentions, everything. If a company or
role is named, its link goes right next to it.

When asked to rank/tier matches, sort by: language confirmed explicitly (not just
absence of a red flag) > stack/skill overlap with the profile > seniority fit >
whether it's a real full-time role in-market (not remote-only/contract/different
role family, e.g. sales/solutions engineering when the search was for engineering
roles). Roughly:
- **Tier 1** — explicit language clearance (or no language requirement in the
  target market), close stack/skill match, seniority ask matches the profile.
- **Tier 2** — explicit clearance but with a real gap (a skill area the candidate
  doesn't use day to day, seniority ask a bit above their level).
- **Tier 3** — bigger caveats: language or work-authorization/sponsorship
  requirement unverified/silent rather than cleared (e.g. Rakuten), contract-only,
  notably higher seniority ask, or a materially different role family (e.g.
  forward-deployed/solutions engineering instead of product engineering).

A listing that explicitly fails either the language check or the work-authorization
check is dropped outright, full stop — it doesn't get demoted to Tier 3, it doesn't
make any tiered list at all.

Always ask, or state your assumption, on: which category/categories to search,
which location(s)/market, and whether to widen beyond the profile's stated skills.
