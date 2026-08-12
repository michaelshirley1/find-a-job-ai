---
name: cv-parsing
description: This skill should be used when the user wants to turn a CV/resume into the candidate profile the job-search skill runs on — triggers on "parse my CV/resume", "build my profile", "set up my job search profile", "use this resume for the job search". Works for any job title or field, not just software engineering. Extracts a structured scripts/profile.json (job titles, locations, years of experience, core vs. secondary/hobby skills, language proficiency, work-authorization status) from an uploaded resume, asking the user for anything the document itself can't answer (target role, target location, target market's language/visa requirements).
version: 1.0.0
---

# CV Parsing (build a candidate profile from a resume)

Turns an uploaded CV/resume into `scripts/profile.json`, the config the
**job-search** skill and `scripts/daily_job_search.py` use to search and grade
listings. See `scripts/profile.example.json` for the exact schema this skill
should produce.

This is not software-engineering-specific — the schema and this process work for
any job title/field JobSpy can search (engineering, design, product, sales,
ops, whatever the user is job-hunting for). Don't assume a tech stack; derive
"skills" from whatever the CV and target role actually call for.

## Inputs this needs

A resume/CV (PDF, text paste, or described in conversation) is the primary
source, but a resume alone usually can't answer everything the profile needs.
Ask the user directly for whatever's missing rather than guessing:

- **Target job title(s)** — a resume shows past roles, not the roles being
  applied to next. If the user hasn't said, ask (or infer conservatively from
  their most recent title(s) and confirm with them before finalizing).
- **Target location(s) and market** — a resume rarely states where the person
  wants to work next. Ask for city/country, and whether this is a relocation
  search (which triggers the language/work-authorization questions below) or a
  local/domestic search (which usually doesn't).
- **Target market's language requirement, if relocating** — see "Language
  proficiency" below; a resume's "Languages" section is a start but usually
  isn't precise enough on its own.
- **Work-authorization status for the target market** — almost never fully
  answerable from the resume alone; always confirm directly with the user (see
  below).

## Extracting the profile fields

### `years_experience`

Count actual professional (paid, production) experience relevant to the target
role(s) — not time spent studying, not personal projects, not internships unless
the user wants those counted. If experience spans multiple roles/stacks, use
judgment on what's relevant to the specific `job_titles` being targeted and say
so; don't just sum total career length if a chunk of it is in an unrelated field.

### `core_skills` vs `secondary_skills` — the most important distinction

This is the single most consequential judgment call in the whole profile, and
it's easy to get wrong by taking a resume's "Skills" section at face value.

- **`core_skills`**: things the candidate has genuinely used in a **production,
  paid, shipped** capacity — the tools/languages/frameworks their actual job
  history shows them working in day to day.
- **`secondary_skills`**: things that are real (the candidate isn't lying about
  knowing them) but are **hobby-only, self-taught, or personal-project-only** —
  no production experience shipping/operating them professionally, even if
  they're listed in a "Skills" section or the candidate is genuinely competent
  with them.

A resume's flat "Skills: Python, Go, React, TypeScript, C#, ..." list does
**not** tell you which bucket each one belongs in — cross-reference against the
actual work-history bullets. If the work history only ever mentions React/C#/
TypeScript in shipped-project descriptions, and Python only shows up as "personal
projects" or doesn't appear in the work history at all despite being in the
skills list, that's a `core_skills` vs `secondary_skills` split, not two core
skills. When genuinely ambiguous, ask the candidate rather than guessing — the
downside of silently crediting a hobby skill as core is a pile of results the
candidate isn't actually qualified for; the downside of asking is one extra
question.

Assign weights (roughly 3-10, matching `scripts/profile.example.json`) by how
central the skill is to the roles being targeted — e.g. the primary
language/framework of the target job title(s) should outweigh a supporting
tool. `core_stack_max` (default 40) and `secondary_stack_cap` (default 16) don't
usually need to change from the example.

### Language proficiency (only relevant for relocation/foreign-market searches)

Same self-assessed-vs-certified trap as skills, but higher stakes since it can
silently disqualify or silently pass a listing:

- Only set `language_requirements.certified_level` to a level the candidate
  **holds an actual certificate for** (e.g. a JLPT/CEFR/TOEFL result). Self-taught
  study, "conversational" self-rating, or vocab/grammar practice **without** a
  held certificate is not the same thing — leave `certified_level` null/unset in
  that case, even if the candidate feels ready for a given level. A job posting
  that names a specific certified level as a requirement is a real mismatch for
  someone who can't document it, regardless of how close their actual ability is.
- Ask the candidate directly: "Do you hold a certified language qualification for
  <target market's language>, and at what level?" Don't infer a level from
  "I've been studying for two years."
- If the target market has no language requirement relevant to the job search
  (e.g. a domestic search, or a market where the job listings are in the
  candidate's native language), set `target_language` to `null`/omit it —
  `daily_job_search.py` skips language filtering entirely when no module is
  configured for the target language.

### Work authorization

Also easy to round up inaccurately — be precise about the difference between
"eligible to apply for" and "currently holds":

- `status`: use a plain, accurate label — e.g. `"citizen"`, `"authorized"`
  (already holds the right to work there), `"needs_sponsorship"` (needs an
  employer to sponsor/petition), `"eligible_not_authorized"` (meets visa
  eligibility criteria but doesn't hold work authorization yet and needs a
  sponsoring employer to get it).
- `notes`: capture the real nuance in a sentence — e.g. "Eligible for the
  Engineer/Specialist in Humanities visa, self-funding the move, needs an
  employer willing to be the sponsor/petitioner." Don't compress "eligible and
  paperwork-ready" into "has a visa."
- `target_country` / `country_adjective`: only needed for a relocation search;
  omit for a domestic search.

Ask the candidate directly rather than inferring this from the resume — resumes
essentially never state visa/work-authorization status.

### `job_titles` and `locations`

Populate directly from what the user tells you they're targeting (see "Inputs
this needs" above). If they give one title/location, that's fine — the arrays
just support searching multiple at once.

### `market` / `country_indeed`

Set `market` to `"japan"` only for an actual Japan-relocation search (this is
what enables the TokyoDev/Japan Dev/Daijob scrapers and the Japanese-language
filter module in `daily_job_search.py` — see the **job-search** skill). Use
`"other"` (or any non-`"japan"` value) for every other market; `daily_job_search.py`
runs JobSpy-only with no language filtering in that case, which is honest since no
other market's requirement-phrasing detector is implemented yet. Set
`country_indeed` to whatever JobSpy's `country_indeed` param expects for that
market (e.g. `"Japan"`, `"USA"`, `"UK"`).

## Output

Write the result to `scripts/profile.json` (create the file; it's gitignored, so
this is safe to do per-user without polluting the repo). Validate it's valid
JSON and matches the shape in `scripts/profile.example.json` before finishing.
Show the user a short summary of what you extracted/assumed vs. what they told
you directly, especially the core/secondary skill split and the
language/work-authorization fields — those are the ones most likely to need a
correction.

If `scripts/profile.json` already exists, treat this as an update: merge in
whatever the new CV/conversation adds or changes rather than silently
discarding fields (like target job titles or a market) that came from an
earlier conversation and are still accurate, unless the user is clearly
starting a fresh search.
