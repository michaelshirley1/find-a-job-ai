// Package jobspy shells out to the Python JobSpy library (github.com/speedyapply/JobSpy)
// via scripts/jobspy_search.py, since JobSpy has no Go equivalent.
package jobspy

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"

	"github.com/michaelgov-ctrl/find-a-job/internal/models"
)

// SearchParams configures a JobSpy scrape.
type SearchParams struct {
	SearchTerm    string
	Location      string
	Sites         []string // e.g. ["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"]
	ResultsWanted int
	HoursOld      int // 0 means unset / no filter
	CountryIndeed string
}

// rawJob mirrors the JSON fields JobSpy emits for each scraped listing.
type rawJob struct {
	ID          string `json:"id"`
	Site        string `json:"site"`
	Title       string `json:"title"`
	Company     string `json:"company"`
	Location    string `json:"location"`
	JobURL      string `json:"job_url"`
	Description string `json:"description"`
	DatePosted  string `json:"date_posted"`
}

// pythonExe and scriptPath are resolved once but overridable via env vars, since the
// Python interpreter and script location vary across dev machines and deployments.
func pythonExe() string {
	if v := os.Getenv("JOBSPY_PYTHON"); v != "" {
		return v
	}
	return "python3"
}

func scriptPath() string {
	if v := os.Getenv("JOBSPY_SCRIPT"); v != "" {
		return v
	}
	return "scripts/jobspy_search.py"
}

// Search runs the JobSpy CLI wrapper and returns the scraped listings as Jobs.
func Search(ctx context.Context, p SearchParams) ([]models.Job, error) {
	if p.SearchTerm == "" {
		return nil, fmt.Errorf("jobspy: search term is required")
	}

	sites := p.Sites
	if len(sites) == 0 {
		sites = []string{"indeed", "linkedin", "zip_recruiter", "glassdoor", "google"}
	}
	resultsWanted := p.ResultsWanted
	if resultsWanted <= 0 {
		resultsWanted = 20
	}
	countryIndeed := p.CountryIndeed
	if countryIndeed == "" {
		countryIndeed = "USA"
	}

	args := []string{
		scriptPath(),
		"--search-term", p.SearchTerm,
		"--location", p.Location,
		"--site-name", strings.Join(sites, ","),
		"--results-wanted", strconv.Itoa(resultsWanted),
		"--country-indeed", countryIndeed,
	}
	if p.HoursOld > 0 {
		args = append(args, "--hours-old", strconv.Itoa(p.HoursOld))
	}

	cmd := exec.CommandContext(ctx, pythonExe(), args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("jobspy: scrape failed: %w: %s", err, stderr.String())
	}

	var raw []rawJob
	if err := json.Unmarshal(stdout.Bytes(), &raw); err != nil {
		return nil, fmt.Errorf("jobspy: parsing output: %w", err)
	}

	jobs := make([]models.Job, 0, len(raw))
	for _, r := range raw {
		jobs = append(jobs, models.Job{
			ID:          r.ID,
			Title:       r.Title,
			Company:     r.Company,
			Location:    r.Location,
			Description: r.Description,
			URL:         r.JobURL,
			CreatedAt:   r.DatePosted,
		})
	}
	return jobs, nil
}
