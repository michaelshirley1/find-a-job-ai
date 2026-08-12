# find-a-job

A configurable job search pipeline: scrapes Indeed/LinkedIn via
[JobSpy](https://github.com/speedyapply/JobSpy) for whatever job title(s) and
location(s) you're targeting, optionally adds Japan-focused boards (TokyoDev,
Japan Dev, CareerCross, Daijob) for a Japan relocation search, then filters and
grades (A-F) every listing against your own candidate profile — skills, years of
experience, language requirements, work authorization. Everything is driven by a
profile, not hardcoded to any one person or job title.

## Setup

```
pip install -r scripts/requirements.txt
cp scripts/profile.example.json scripts/profile.json
```

Edit `scripts/profile.json` (see the schema in `scripts/profile.example.json`)
yourself, or, in Claude Code, upload your resume and run:

```
/cv-parsing
```

## Running a search

In Claude Code, run:

```
/job-search
```

This uses your `scripts/profile.json` to run `scripts/daily_job_search.py` for
the default daily pipeline, or does an ad-hoc/manual search (a one-off title,
city, or market outside your profile's defaults) if that's what you ask for —
it knows the scraping gotchas for each source either way.

You can also run the daily pipeline directly, without Claude Code:

```
python scripts/daily_job_search.py                     # default run
python scripts/daily_job_search.py --hours-old 48       # widen the recency window
python scripts/daily_job_search.py --show-all           # report everything, not just new-since-last-run
python scripts/daily_job_search.py --profile scripts/other_profile.json
```

State lives in `data/seen_jobs.json` (so re-runs only report genuinely new
listings) and each run writes a dated report to `data/reports/YYYY-MM-DD.md`.
Both are gitignored — they're your personal search history, not repo content.

## How grading works

Every surviving listing is scored 0-100: stack/skill fit (0-40, capped low if the
match is only on your secondary/hobby skills), language clearance (0-20),
work-authorization clearance (0-20), seniority fit (0-10), role-family fit
(0-10). A ≥85, B ≥70, C ≥55, D ≥40, F <40. Listings that fail the language or
work-authorization check outright are excluded, not graded down.

## Repo layout

- `scripts/daily_job_search.py` — the pipeline: scrape, filter, grade, dedupe, report.
- `scripts/profile.example.json` — the candidate profile schema; copy to `profile.json` (gitignored) and fill in.
- `scripts/requirements.txt` — Python dependencies.
- `.claude/skills/cv-parsing/` — builds `scripts/profile.json` from a resume.
- `.claude/skills/job-search/` — runs searches (automated or ad-hoc) and documents the scraping gotchas for each source.
- `data/` — gitignored run state and reports, created on first run.
