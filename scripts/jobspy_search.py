"""CLI wrapper around python-jobspy: reads search args, prints job results as JSON on stdout.

Invoked as a subprocess by internal/jobspy (see jobspy.go) rather than imported directly,
since JobSpy is a Python-only library with no Go equivalent.
"""
import argparse
import json
import sys

from jobspy import scrape_jobs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-term", required=True)
    parser.add_argument("--location", default="")
    parser.add_argument("--site-name", default="indeed,linkedin,zip_recruiter,glassdoor,google")
    parser.add_argument("--results-wanted", type=int, default=20)
    parser.add_argument("--hours-old", type=int, default=None)
    parser.add_argument("--country-indeed", default="USA")
    args = parser.parse_args()

    try:
        jobs_df = scrape_jobs(
            site_name=[s.strip() for s in args.site_name.split(",") if s.strip()],
            search_term=args.search_term,
            location=args.location or None,
            results_wanted=args.results_wanted,
            hours_old=args.hours_old,
            country_indeed=args.country_indeed,
        )
    except Exception as exc:  # noqa: BLE001 - surface any scrape failure to the Go caller
        print(f"jobspy scrape failed: {exc}", file=sys.stderr)
        return 1

    jobs_df = jobs_df.where(jobs_df.notnull(), None)
    print(json.dumps(jobs_df.to_dict(orient="records"), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
