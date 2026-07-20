package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/michaelgov-ctrl/find-a-job/internal/jobspy"
)

const searchTimeout = 60 * time.Second

func SearchJobs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	term := q.Get("term")
	if term == "" {
		http.Error(w, "term query param is required", http.StatusBadRequest)
		return
	}

	params := jobspy.SearchParams{
		SearchTerm:    term,
		Location:      q.Get("location"),
		CountryIndeed: q.Get("country"),
	}
	if sites := q.Get("sites"); sites != "" {
		params.Sites = strings.Split(sites, ",")
	}
	if v := q.Get("results"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			params.ResultsWanted = n
		}
	}
	if v := q.Get("hours_old"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			params.HoursOld = n
		}
	}

	ctx, cancel := context.WithTimeout(r.Context(), searchTimeout)
	defer cancel()

	jobs, err := jobspy.Search(ctx, params)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(jobs)
}
